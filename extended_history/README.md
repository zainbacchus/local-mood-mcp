# Drop your Extended Streaming History here

This folder is where you place Spotify's **Extended Streaming History** export to
unlock the *lifetime* moods (`morning`, `late_night`, `weekend`, `on_repeat`,
`comfort`, `focus_flow`, `deep_cuts`).

## How to get the export
1. Go to <https://www.spotify.com/account/privacy/>.
2. Tick **"Extended streaming history"** → **Request data**.
3. Spotify emails a download link in **~5 days** (sometimes up to 30).

## How to use it
1. Unzip the download.
2. Drop the JSON files (the `Streaming_History_Audio_*.json` files — or the whole
   unzipped folder; subfolders are scanned recursively) **into this folder**.
3. Run the `sync_listening_history` tool. It auto-detects and merges what's here.
   (Or run `import_extended_history` directly.)
4. The lifetime moods are now available to `generate_playlist` /
   `create_mood_playlist`.

## ⚠️ Privacy
The export contains **personal data** — IP addresses, timestamps, and your full
listening history. Everything you drop here is **git-ignored** and will never be
committed. Only this README and the `.gitkeep` placeholder are tracked. Keep it
that way; do not force-add the data files.
