// The two config keys that keep the built app off Google's network, and the
// file one of them points at.
//
// **This is a regression guard, not the check itself.** Whether the running
// app contacts an external origin can only be answered by a browser --
// `performance.getEntriesByType('resource')` filtered against
// `location.origin`, the procedure in README.md and DESIGN.md. What these
// tests defend is the *mechanism*: `web/flutter_bootstrap.js` is a file
// Flutter will silently regenerate without these keys if it goes missing,
// and the vendored fallback font is a 171 KB binary at a path nobody would
// guess was load-bearing. Both are easy to lose in a tidy-up, and losing
// either restores a request to Google that nothing else in the suite notices.
//
// Deliberately *not* a grep of `build/web` for external hosts. That output
// carries thirty-odd absolute URLs, nearly all licence text in `NOTICES`,
// plus `www.gstatic.com` in `flutter.js` as the branch `canvasKitBaseUrl`
// short-circuits -- so a grep fails on a clean build, and a check that cries
// wolf gets deleted.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// Where the engine looks for its default font fallback: the value of
/// `fontFallbackBaseUrl` followed by the path compiled into `main.dart.js`.
/// Both halves have to agree or the request silently goes to Google.
const String fallbackFont =
    'web/fallback-fonts/roboto/v32/KFOmCnqEu92Fr1Me4GZLCzYlKw.woff2';

void main() {
  late String bootstrap;

  setUpAll(() {
    final File file = File('web/flutter_bootstrap.js');
    expect(
      file.existsSync(),
      isTrue,
      reason: 'web/flutter_bootstrap.js is gone; Flutter will generate a '
          'default one that fetches CanvasKit and Roboto from Google',
    );
    bootstrap = file.readAsStringSync();
  });

  test('canvaskit is served from our own origin', () {
    // Without this the loader falls through to
    // https://www.gstatic.com/flutter-canvaskit/<engineRevision>/ -- while
    // the build has already copied CanvasKit into build/web/canvaskit/.
    expect(bootstrap, contains('canvasKitBaseUrl: "canvaskit/"'));
  });

  test('the font fallback is served from our own origin', () {
    // Defaults to https://fonts.gstatic.com/s/ and is fetched eagerly, even
    // though every glyph on screen comes from bundled Inter.
    expect(bootstrap, contains('fontFallbackBaseUrl: "fallback-fonts/"'));
  });

  test('the vendored fallback font exists where the engine will ask for it', () {
    final File font = File(fallbackFont);
    expect(
      font.existsSync(),
      isTrue,
      reason: 'the engine appends roboto/v32/KFOmCnqEu92Fr1Me4GZLCzYlKw.woff2 '
          'to fontFallbackBaseUrl; a 404 here sends it back to Google',
    );
    // A TrueType font under a .woff2 name, on purpose -- the name has to
    // match what the engine appends, and Skia sniffs the container rather
    // than the extension. `0x00010000` is the TrueType magic; asserting it
    // catches a placeholder or a truncated copy, which the existence check
    // alone would not.
    expect(font.readAsBytesSync().sublist(0, 4), <int>[0x00, 0x01, 0x00, 0x00]);
  });
}
