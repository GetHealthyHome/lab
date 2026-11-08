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

if __name__ == "__main__":
    conn = db.connect(sys.argv[1] if len(sys.argv) > 1 else "kiosk.db")
    conn.executescript(DEMO)
    print("Seeded: IDs ID-1001, ID-1002; tools T-0001, T-0002")
