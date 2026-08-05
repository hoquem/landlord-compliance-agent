// Rendering ids for humans: category labels and money.
//
// `categoryLabel` is derived from the id rather than looked up in a map,
// deliberately — a map is a second list of categories, and the failure mode
// of one being updated and not the other is a blank cell in a tax document.
// The acronym set below is not that second list: it is a spelling rule about
// three letters, and adding a category never requires touching it.
import 'package:flutter_test/flutter_test.dart';

import 'package:landlord_compliance/api/money.dart';

void main() {
  group('categoryLabel', () {
    test('turns an id into a sentence', () {
      expect(categoryLabel('rent_income'), 'Rent income');
      expect(categoryLabel('replacement_domestic_items'),
          'Replacement domestic items');
      expect(categoryLabel('gas_safety'), 'Gas safety');
    });

    test('keeps acronyms as acronyms', () {
      // Seen on a real screen 2026-08-05: rows read "Epc" and "Eicr" directly
      // under a subtitle that read "Gas safety, EICR, EPC and licences" — the
      // same screen disagreeing with itself about how to spell a word.
      expect(categoryLabel('epc'), 'EPC');
      expect(categoryLabel('eicr'), 'EICR');
    });

    test('an acronym inside a longer id is still an acronym', () {
      expect(categoryLabel('epc_certificate'), 'EPC certificate');
    });

    test('an empty id renders as nothing, not a crash', () {
      expect(categoryLabel(''), '');
    });
  });
}
