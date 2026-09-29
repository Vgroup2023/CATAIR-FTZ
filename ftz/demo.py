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
    a = s.save_admission({
        "zone_id": zone, "operator_id": op, "carrier_id": car, "importer_id": imp, "transport_mode": "truck",
        "transport_doc": "DEMO-BL-1001", "port_of_entry": "5301", "entry_date": d(-14),
        "lines": [
            {"description": "Bluetooth speakers", "htsus": "8518.22.0000", "coo": "CN", "qty": 500, "uom": "PCS", "value": 12500,
             "zone_status": "PF", "duty_rate": 4.9, "location": "A-01"},
            {"description": "Cotton T-shirts", "htsus": "6109.10.0004", "coo": "VN", "qty": 2000, "uom": "PCS", "value": 9000,
             "zone_status": "NPF", "location": "B-04"},
            {"description": "Scrap-bound defective chargers", "htsus": "8504.40.9540", "coo": "CN", "qty": 120, "uom": "PCS", "value": 600,
             "zone_status": "ZR", "location": "Q-01"},
        ]})
    s.admission_action(a["id"], "submit")
    s.admission_action(a["id"], "approve", {"cbp_ref": "DEMO-ACE-0001"})
    s.admission_action(s.save_admission({
        "zone_id": zone, "operator_id": op, "carrier_id": car, "transport_mode": "truck", "transport_doc": "DEMO-BL-1002",
        "port_of_entry": "5301", "entry_date": d(0),
        "lines": [{"description": "USB-C cables", "htsus": "8544.42.9090", "coo": "CN", "qty": 300, "uom": "PCS", "value": 900,
                   "zone_status": "PF", "duty_rate": 2.6}]})["id"], "submit")
    p = s.save_permit({"zone_id": zone, "operator_id": op, "kind": "blanket", "valid_from": d(-30), "valid_to": d(300),
                       "activities": ["manipulate", "manufacture", "exhibit", "destroy", "temp_removal"],
                       "description": "Kitting, relabeling and destruction of defective goods."})
    s.permit_action(p["id"], "activate", {"cbp_ref": "DEMO-216-0001"})
    lot = s.list_lots()[-1]
    s.create_inbond({"type": "TE", "zone_id": zone, "carrier_id": car, "consignee_id": con_, "mode": "truck", "origin_port": "5301",
                     "dest_port": "2704", "bl_no": "DEMO-TE-77", "issued_date": d(-2), "lines": [{"lot_id": lot["id"], "qty": 100}]})
    con.commit()
    return True
