# Lab Toolbox

Self-service tool checkout kiosk for the Healthy Home Academy lab: a Raspberry Pi at the cage gate logs who took each tool, watches the cage for empty spots, and syncs to a Google Sheet with a staff dashboard.

The kiosk service lives in [`kiosk/`](kiosk/README.md); the staff dashboard (a Google Apps Script web app on the Sheet) lives in [`dashboard/`](dashboard/README.md).

The earlier Next.js + Supabase app was removed from this repo; it is still in git history (last version at commit `c794d24`).
