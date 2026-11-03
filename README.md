# Lab Tool Tracker

Mobile-first PWA for checking tools and kits in and out of the hands-on HVAC lab by scanning their barcodes.

## Stack
Next.js (App Router) + Tailwind, Supabase (Postgres, RLS, email + password auth), ZXing camera scanning, IndexedDB outbox for offline scans.

## Run it
```bash
cp .env.example .env.local   # fill in the server-only values if you want the nightly email
npm install
npm run dev
```
Sign in with the email and temporary password your admin gave you; the app makes you choose a new password on first sign-in. Admins create logins on the Admin page ("Add a person" with a temporary password), which calls the `admin-create-user` edge function in `supabase/functions/`.

## How it works
- **Codes.** A scanned value is matched against `assets.barcode`, `assets.serial`, `assets.id`, `locations.code` and `people.badge_code`. Print QR stickers whose value is the code you typed when adding the item.
- **Check out.** Scan a tool, tap a name (or scan a badge / truck bin label), confirm. Default due time is end of today. Scanning a kit lists its contents; untick anything missing and it is flagged `lost`.
- **Check in.** Scan the tool, rate its condition. Scan a shelf label first to deposit many tools into that bin in a row; otherwise the tool returns to its home bin.
- **Audit.** Scan a bin label, then every tool in it; the list shows what is still unaccounted for.
- **Offline.** Lookup tables are cached in IndexedDB on every page load. Scan actions made offline queue up and replay in order when the network returns.
- **Nightly report.** `GET /api/reports/overdue` (Vercel cron at 03:00 UTC, authenticated with `CRON_SECRET`) emails admins and trainers the list of tools still out past their due time. Needs `SUPABASE_SERVICE_ROLE_KEY` and `RESEND_API_KEY`.

## Database
Schema lives in `supabase/migrations/` and is already applied to the "Lab Tool Tracker" Supabase project. Every scan action goes through the `record_movement()` Postgres function so kits, status, custody and history are updated atomically.
