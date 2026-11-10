"""Fill a fresh database with a few demo rows for bench testing: python -m lab_toolbox.seed kiosk.db"""
import sys

from . import db

DEMO = """
INSERT INTO spots VALUES ('LOC-S03-B02-H04', 'Shelf 03, bay 02, hook 04', NULL, NULL);
INSERT INTO spots VALUES ('LOC-S01-B01-NA', 'Shelf 01, bay 01', NULL, NULL);
INSERT INTO tools (asset_id, qr_code, name, category, spot_id) VALUES
  ('HHA-0001', 'T-0001', 'Digital multimeter', 'Electrical', 'LOC-S03-B02-H04'),
  ('HHA-0002', 'T-0002', 'Manifold gauge set', 'HVAC', 'LOC-S01-B01-NA');
INSERT INTO people (person_id, id_code, name, role) VALUES
  ('P-1', 'ID-1001', 'Jordan', 'student'),
  ('P-2', 'ID-1002', 'Sam', 'student');
"""

# More demo rows for trying the screen by hand (battery pairing, a kit, instructor mode). Tests use DEMO only.
SCREEN_DEMO = """
INSERT INTO spots VALUES ('LOC-S02-B01-CHG01', 'Shelf 02, bay 01, charger 01', NULL, NULL);
INSERT INTO battery_groups VALUES ('DW20', 'DeWalt 20V');
INSERT INTO tools (asset_id, qr_code, name, category, spot_id, needs_battery, battery_group_id, is_battery) VALUES
  ('HHA-0003', 'T-0003', 'Cordless drill', 'Power tools', 'LOC-S01-B01-NA', 1, 'DW20', 0),
  ('HHA-0004', 'T-0004', 'DeWalt 20V battery #1', 'Batteries', 'LOC-S02-B01-CHG01', 0, 'DW20', 1),
  ('HHA-0005', 'T-0005', 'Flare tool', 'HVAC', 'LOC-S01-B01-NA', 0, NULL, 0),
  ('HHA-0006', 'T-0006', 'Tubing cutter', 'HVAC', 'LOC-S01-B01-NA', 0, NULL, 0);
INSERT INTO kits (kit_id, case_code, name, spot_id) VALUES ('KIT-1', 'K-0001', 'Flaring kit', 'LOC-S01-B01-NA');
INSERT INTO kit_items VALUES ('KIT-1', 'HHA-0005'), ('KIT-1', 'HHA-0006');
INSERT INTO people (person_id, id_code, name, role) VALUES
  ('P-9', 'ID-9001', 'Ms. Rivera', 'instructor');
"""

if __name__ == "__main__":
    conn = db.connect(sys.argv[1] if len(sys.argv) > 1 else "kiosk.db")
    conn.executescript(DEMO + SCREEN_DEMO)
    print("Seeded: students P-1, P-2; instructor P-9; tools T-0001..T-0006 (T-0003 drill needs T-0004 battery); kit case K-0001."
          " Give PINs with: python -m lab_toolbox.pins P-1")
