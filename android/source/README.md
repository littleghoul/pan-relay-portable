# Pan Relay Portable Android
Android 8.0+. Package: com.panrelay.portable. No default server, credentials or API tokens.
First launch requires a primary server address; an external address is optional. Use Settings to change them. HTTP and HTTPS root URLs are supported. Third-party pages open externally; no native JavaScript bridge.
Build on Windows with JDK 17, Android SDK 36 and Build Tools 35.0.0: `python build.py`.
The signing key is generated outside this source directory under ../private/pan-relay-portable-signing. Never distribute the key or runtime data. Reuse the key for updates.
Excel downloads use Android's save-file picker, with an 8 MB limit.
