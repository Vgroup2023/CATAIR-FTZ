"""Supporting documents (invoices, bills of lading, permits, CBP notices) stored with the records they belong to.

Stored in the database, so they are encrypted at rest, backed up and covered by the same access rules as everything else.
Each file gets a SHA-256 fingerprint when it is stored and is re-checked every time it is downloaded. Documents are never deleted:
a wrong or superseded file is voided with a reason and stays on record (trade records must be kept for years).
"""
import base64
import binascii
import hashlib
import re

from .common import ApiError, need
from .db import audit, now, one, rows

MAX_BYTES = 8 * 1024 * 1024
MAX_PER_ENTITY = 200
ENTITIES = {"admission": "admissions", "permit": "permits", "inbond": "inbonds", "lot": "lots", "filing": "ft_filings", "general": None}
KINDS = ("invoice", "packing_list", "bill_of_lading", "cbp_notice", "permit", "bond", "certificate", "correspondence", "other")
# extension -> (content type, check that the bytes really are that kind of file)
TYPES = {
    "pdf": ("application/pdf", lambda b: b.startswith(b"%PDF-")),
    "png": ("image/png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    "jpg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    "jpeg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    "csv": ("text/csv", lambda b: _text(b)),
    "txt": ("text/plain", lambda b: _text(b)),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", lambda b: b.startswith(b"PK\x03\x04")),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", lambda b: b.startswith(b"PK\x03\x04")),
    "xls": ("application/vnd.ms-excel", lambda b: b.startswith(b"\xd0\xcf\x11\xe0")),
    "doc": ("application/msword", lambda b: b.startswith(b"\xd0\xcf\x11\xe0")),
}
LIST_COLS = "id, entity, entity_id, kind, filename, content_type, size, sha256, note, uploaded_by, uploaded_at, voided_at, voided_by, void_reason"


def _text(b):
    if b"\x00" in b:
        return False
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def clean_name(name):
    name = str(name or "").replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[^\w.\- ()]", "_", name)[:120].strip(". ")
    return name


class DocumentsMixin:
    def doc_spec(self):
        return {"max_bytes": MAX_BYTES, "kinds": list(KINDS), "types": sorted(TYPES), "entities": [e for e in ENTITIES if e != "general"] + ["general"]}

    def doc_list(self, entity=None, entity_id=None, include_voided=True, q=None):
        sql, args = f"SELECT {LIST_COLS} FROM documents WHERE 1=1", []
        if entity:
            need(entity in ENTITIES, "unknown record type")
            sql += " AND entity=?"
            args.append(entity)
            if entity_id not in (None, ""):
                need(str(entity_id).isdigit(), "record id must be a number")
                sql += " AND entity_id=?"
                args.append(int(entity_id))
        if not include_voided:
            sql += " AND voided_at IS NULL"
        if q:
            sql += " AND (filename LIKE ? OR note LIKE ? OR kind LIKE ?)"
            args += [f"%{q}%"] * 3
        return rows(self.con, sql + " ORDER BY id DESC LIMIT 500", args)

    def doc_upload(self, d):
        entity, eid = str(d.get("entity") or ""), d.get("entity_id")
        need(entity in ENTITIES, "choose what the document belongs to")
        table = ENTITIES[entity]
        if table:
            need(str(eid).isdigit() and one(self.con, f"SELECT id FROM {table} WHERE id=?", (int(eid),)), "that record does not exist")
            eid = int(eid)
        else:
            eid = 0
        name = clean_name(d.get("filename"))
        need(name and "." in name, "the file needs a name with its extension, such as invoice.pdf")
        ext = name.rsplit(".", 1)[1].lower()
        need(ext in TYPES, "this file type is not accepted. Allowed: " + ", ".join(sorted(TYPES)))
        try:
            data = base64.b64decode(str(d.get("data") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise ApiError("the file could not be read")
        need(data, "the file is empty")
        need(len(data) <= MAX_BYTES, f"the file is larger than {MAX_BYTES // 1024 // 1024} MB")
        ctype, looks_right = TYPES[ext]
        need(looks_right(data), f"this does not look like a real .{ext} file")
        kind = str(d.get("kind") or "other")
        need(kind in KINDS, "unknown document kind")
        digest = hashlib.sha256(data).hexdigest()
        dup = one(self.con, f"SELECT {LIST_COLS} FROM documents WHERE entity=? AND entity_id=? AND sha256=? AND voided_at IS NULL", (entity, eid, digest))
        if dup:
            return {**dup, "duplicate": True}
        n = one(self.con, "SELECT COUNT(*) n FROM documents WHERE entity=? AND entity_id=?", (entity, eid))["n"]
        need(n < MAX_PER_ENTITY, f"this record already has {MAX_PER_ENTITY} documents")
        note = str(d.get("note") or "").strip()[:300] or None
        cur = self.con.execute("""INSERT INTO documents(entity,entity_id,kind,filename,content_type,size,sha256,content,note,uploaded_by,uploaded_at)
                                  VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (entity, eid, kind, name, ctype, len(data), digest, data, note, self.user, now()))
        audit(self.con, self.user, "upload", "document", cur.lastrowid, {"file": name, "entity": entity, "entity_id": eid, "size": len(data), "sha256": digest})
        return one(self.con, f"SELECT {LIST_COLS} FROM documents WHERE id=?", (cur.lastrowid,))

    def doc_download(self, doc_id):
        r = one(self.con, "SELECT * FROM documents WHERE id=?", (doc_id,))
        need(r, "document not found", 404)
        data = bytes(r["content"])
        if hashlib.sha256(data).hexdigest() != r["sha256"]:
            audit(self.con, self.user, "integrity_failure", "document", doc_id, {"file": r["filename"]})
            self.con.commit()
            raise ApiError("this stored document no longer matches its fingerprint, so it was not released. Contact your administrator", 500)
        audit(self.con, self.user, "download", "document", doc_id, {"file": r["filename"], "sha256": r["sha256"]})
        return r["filename"], r["content_type"], data

    def doc_void(self, doc_id, d):
        r = one(self.con, f"SELECT {LIST_COLS} FROM documents WHERE id=?", (doc_id,))
        need(r, "document not found", 404)
        need(not r["voided_at"], "this document is already voided")
        reason = str((d or {}).get("reason") or "").strip()[:300]
        need(len(reason) >= 3, "say why the document is being voided")
        self.con.execute("UPDATE documents SET voided_at=?, voided_by=?, void_reason=? WHERE id=?", (now(), self.user, reason, doc_id))
        audit(self.con, self.user, "void", "document", doc_id, {"file": r["filename"], "reason": reason})
        return one(self.con, f"SELECT {LIST_COLS} FROM documents WHERE id=?", (doc_id,))

    def doc_verify_all(self):
        """Re-fingerprint every stored document (for the integrity report)."""
        bad, total = [], 0
        for r in rows(self.con, "SELECT id, filename, sha256 FROM documents ORDER BY id"):
            total += 1
            data = one(self.con, "SELECT content FROM documents WHERE id=?", (r["id"],))["content"]
            if hashlib.sha256(bytes(data)).hexdigest() != r["sha256"]:
                bad.append({"id": r["id"], "filename": r["filename"]})
        return {"documents": total, "mismatches": bad, "ok": not bad}
