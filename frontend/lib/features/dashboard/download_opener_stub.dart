/// Non-web (VM test) implementation of [openDownloadUrl].
///
/// There is no browser window on the VM, so opening is a no-op. The
/// `dart:html` web implementation (see `download_opener_web.dart`) is what
/// the browser build resolves to.
library;

void openDownloadUrl(String url) {
  // No-op outside the web.
}
