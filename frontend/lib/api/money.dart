/// Formatting money for a ledger.
///
/// Two rules, both from `DESIGN.md`, both easy to get wrong:
///
/// * **A real minus sign** (U+2212), not a hyphen. A hyphen is narrower than
///   a digit, so a column of amounts stops aligning the moment one goes
///   negative — which in a ledger is most of them.
/// * **Never coloured by sign.** An expense is not an error. The sign is
///   carried typographically and nowhere else; `danger` means something is
///   *wrong*, and spending money on a roof is not.
library;

/// U+2212 MINUS SIGN. Digit-width, unlike the hyphen-minus.
const String kMinus = '−';

/// Format [value] as pounds: thousands separated, two decimals, real minus.
String formatMoney(double value) {
  final bool negative = value < 0;
  final String digits = value.abs().toStringAsFixed(2);
  final int dot = digits.indexOf('.');
  final String whole = digits.substring(0, dot);
  final String fraction = digits.substring(dot);

  final StringBuffer grouped = StringBuffer();
  for (int i = 0; i < whole.length; i++) {
    if (i > 0 && (whole.length - i) % 3 == 0) grouped.write(',');
    grouped.write(whole[i]);
  }
  return '${negative ? kMinus : ''}$grouped$fraction';
}

/// Render an HMRC category id for a human: `rent_income` to `Rent income`.
///
/// Derived rather than looked up in a label map. A map is a second list of
/// categories, and the failure mode of one being updated and not the other
/// is a blank cell in a tax document.
String categoryLabel(String id) {
  final String spaced = id.replaceAll('_', ' ');
  return spaced.isEmpty
      ? spaced
      : '${spaced[0].toUpperCase()}${spaced.substring(1)}';
}
