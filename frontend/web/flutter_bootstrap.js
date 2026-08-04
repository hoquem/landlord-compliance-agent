// Custom Flutter web bootstrap.
//
// **Why this file exists: to stop the app contacting Google on every load.**
//
// By default Flutter's generated bootstrap resolves CanvasKit to
// `https://www.gstatic.com/flutter-canvaskit/<engineRevision>/` -- while
// `flutter build web` has *already* copied CanvasKit into `build/web/canvaskit/`.
// So the bytes ship and go unused, and every page load tells Google that
// someone opened a page showing their bank transactions.
//
// Measured 2026-08-04: neither `--dart-define=UseLocalCanvasKit=true` nor
// `--dart-define=FLUTTER_WEB_CANVASKIT_URL=...` changes this. The first is a
// build-system define the CLI does not expose; the second is consumed at
// build time but the *runtime* bootstrap re-derives the URL from
// `engineRevision`. The lever that works is this runtime config key, read in
// the generated loader as
// `canvasKitBaseUrl ? ... : engineRevision && !useLocalCanvasKit ? gstatic : "canvaskit"`.
//
// Verified by `performance.getEntriesByType('resource')` in a real browser,
// not by reading the flag documentation -- which is how the original claim
// came to be wrong in four files at once.
{{flutter_js}}
{{flutter_build_config}}

_flutter.loader.load({
  config: {
    canvasKitBaseUrl: "canvaskit/",
  },
});
