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
//
// **`fontFallbackBaseUrl` closes the second hole (2026-08-04).** Flutter's
// engine eagerly downloads a Roboto face as its default font fallback, from
// `https://fonts.gstatic.com/s/` + `roboto/v32/KFOmCnqEu92Fr1Me4GZLCzYlKw.woff2`
// (the default is compiled into main.dart.js; that exact path is where the
// filename below comes from). Nothing on screen uses it -- Inter and Material
// Icons are both bundled -- but it is fetched anyway, so it leaked an IP on
// every load.
//
// `web/fallback-fonts/roboto/v32/KFOmCnqEu92Fr1Me4GZLCzYlKw.woff2` is
// **Flutter's own `Roboto-Regular.ttf`**, copied out of the SDK's
// `bin/cache/artifacts/material_fonts/`. Nothing was downloaded to build it.
//
// **That file is a TrueType font under a `.woff2` name, deliberately.** The
// name has to match what the engine appends and nothing inspects the
// extension: Skia sniffs the container. Proved rather than assumed --
// `CanvasKit.Typeface.MakeFreeTypeFaceFromData` on the served bytes returned
// a real typeface and resolved non-zero glyph ids for `A a 1 £ —`. "The page
// still looks right" could not have told us; every glyph on screen comes
// from Inter.
//
// Costs 171 KB uncompressed against Google's ~15 KB subsetted woff2. Accepted:
// it is one same-origin request against 5.6 MB of CanvasKit, and the
// alternative is telling Google who is reading their bank statements.
//
// Re-measured after the change, same way: `external: []`. **Unregister the
// service worker and clear `flutter-app-cache` first** -- three consecutive
// "the fix didn't work" readings were the previous build being replayed.
{{flutter_js}}
{{flutter_build_config}}

_flutter.loader.load({
  config: {
    canvasKitBaseUrl: "canvaskit/",
    fontFallbackBaseUrl: "fallback-fonts/",
  },
});
