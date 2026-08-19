/// Opens a URL so the browser fetches it (used for downloads).
///
/// On the web this calls `window.open`, which is what actually hands the
/// browser the signed URL to download. On every other platform it is a
/// no-op — there is no window to open in a VM test, and a `dart:html`
/// import would make the unit-test compilation fail. The conditional
/// import keeps the web path real while the stub keeps tests green.
library;

export 'download_opener_stub.dart'
    if (dart.library.html) 'download_opener_web.dart';
