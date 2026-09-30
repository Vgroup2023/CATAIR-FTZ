"""Sample data so the demo login opens onto a populated system (only when the database is empty)."""
from datetime import date, timedelta

from .db import one
from .service import Service


def seed_demo_data(con):
    if one(con, "SELECT id FROM zones"):
        return False
    s = Service(con, "demo-seed")
    today = date.today()
    d = lambda n: (today + timedelta(days=n)).isoformat()
    zone = s.create_zone({"zone_no": "FTZ 999", "name": "Demo Zone - Gulf Coast", "grantee": "Demo Port Authority", "port_code": "5301"})["id"]
    party = lambda k, n, i=None: s.create_party({"kind": k, "name": n, "ident": i})["id"]
    op, car = party("operator", "Demo Zone Operator LLC", "EIN 00-0000000"), party("carrier", "Demo Freight Lines", "DFLN")
    imp, con_ = party("importer", "Demo Importer Inc.", "IOR 00-0000000"), party("consignee", "Demo Consignee GmbH")

    # Product master: what "smart mapping" fills into e214 lines from a part number.
    for p in [
        dict(part_no="SPK-100", description="Bluetooth speakers", htsus="8518.22.0000", coo="CN", uom="PCS", uom2="KG", conv=0.35,
             duty_rate=4.9, default_status="PF", pga_agencies="FCC"),
        dict(part_no="TEE-200", description="Cotton T-shirts", htsus="6109.10.0004", coo="VN", uom="PCS", uom2="KG", conv=0.18, default_status="NPF"),
        dict(part_no="CHG-300", description="Defective chargers (scrap-bound)", htsus="8504.40.9540", coo="CN", uom="PCS", default_status="ZR"),
        dict(part_no="CBL-400", description="USB-C cables", htsus="8544.42.9090", coo="CN", uom="PCS", duty_rate=2.6, default_status="PF", pga_agencies="FCC"),
    ]:
        s.upsert_part(p, source="demo")

    def admit(bl, day, lines, approve=True):
        a = s.save_admission({"zone_id": zone, "operator_id": op, "carrier_id": car, "importer_id": imp, "transport_mode": "truck",
                              "transport_doc": bl, "port_of_entry": "5301", "entry_date": d(day), "lines": lines})
        s.admission_action(a["id"], "submit")
        if approve:
            s.admission_action(a["id"], "approve", {"cbp_ref": f"DEMO-ACE-{bl[-4:]}"})
        return a

    # Two lots of the same speaker, so FIFO has something to enforce; customs units (KG) come from the master.
    admit("DEMO-BL-0900", -40, [{"part_no": "SPK-100", "qty": 200, "value": 4800, "location": "A-01", "pga_ref": "FCC-DECL-0900"}])
    admit("DEMO-BL-1001", -14, [
        {"part_no": "SPK-100", "qty": 500, "value": 12500, "location": "A-02", "pga_ref": "FCC-DECL-1001"},
        {"part_no": "TEE-200", "qty": 2000, "value": 9000, "location": "B-04"},
        {"part_no": "CHG-300", "qty": 120, "value": 600, "location": "Q-01"},
    ])
    admit("DEMO-BL-1002", 0, [{"part_no": "CBL-400", "qty": 300, "value": 900, "pga_ref": "FCC-DECL-1002"}], approve=False)

    p = s.save_permit({"zone_id": zone, "operator_id": op, "kind": "blanket", "valid_from": d(-30), "valid_to": d(300),
                       "activities": ["manipulate", "manufacture", "exhibit", "destroy", "temp_removal"],
                       "description": "Kitting, relabeling and destruction of defective goods."})
    s.permit_action(p["id"], "activate", {"cbp_ref": "DEMO-216-0001"})
    oldest = next(l for l in s.list_lots() if l["received_on"] == d(-40))
    s.create_inbond({"type": "TE", "zone_id": zone, "carrier_id": car, "consignee_id": con_, "mode": "truck", "origin_port": "5301",
                     "dest_port": "2704", "bl_no": "DEMO-TE-77", "issued_date": d(-2), "lines": [{"lot_id": oldest["id"], "qty": 100}]})
    s.withdraw({"lot_id": oldest["id"], "kind": "consumption", "qty": 40, "date": d(-1), "entry_no": "DEMO-7501-001"})

    # WMS feeds: receiving logs (one short, one with no e214 yet) and a physical count with a small shortage.
    s.import_receipts({"rows": [
        {"receipt_no": "RCV-9001", "part_no": "SPK-100", "qty": 200, "uom": "PCS", "received_on": d(-40), "ref": "DEMO-BL-0900"},
        {"receipt_no": "RCV-1001", "part_no": "SPK-100", "qty": 500, "uom": "PCS", "received_on": d(-14), "ref": "DEMO-BL-1001"},
        {"receipt_no": "RCV-1002", "part_no": "TEE-200", "qty": 2000, "uom": "PCS", "received_on": d(-14), "ref": "DEMO-BL-1001"},
        {"receipt_no": "RCV-1003", "part_no": "CHG-300", "qty": 120, "uom": "PCS", "received_on": d(-14), "ref": "DEMO-BL-1001"},
        {"receipt_no": "RCV-1101", "part_no": "CBL-400", "qty": 290, "uom": "PCS", "received_on": d(0), "ref": "DEMO-BL-1002"},
        {"receipt_no": "RCV-2000", "part_no": "CBL-400", "qty": 80, "uom": "PCS", "received_on": d(0), "ref": "DEMO-BL-2000"},
    ]}, source="demo")
    s.import_wms_inventory({"snapshot_on": d(0), "rows": [
        {"part_no": "SPK-100", "qty": 557, "uom": "PCS"}, {"part_no": "TEE-200", "qty": 2000, "uom": "PCS"},
        {"part_no": "CHG-300", "qty": 120, "uom": "PCS"}]}, source="demo")
    s.run_reconciliation("demo")
    con.commit()
    return True
