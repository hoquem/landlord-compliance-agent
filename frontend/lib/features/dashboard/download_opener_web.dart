/// Web implementation of [openDownloadUrl]: opens the URL in a new tab.
///
/// The signed document URL is a GET; `window.open(..., '_blank')` is what
/// hands the browser the document to download.
library;

import 'dart:html' as html;

void openDownloadUrl(String url) {
  html.window.open(url, '_blank');
}
