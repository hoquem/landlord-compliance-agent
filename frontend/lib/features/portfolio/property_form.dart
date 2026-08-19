/// Creating a property. Inline, like every other form here.
///
/// The address is required (the property has to be findable), everything
/// else has a sensible default the backend supplies if we leave it out —
/// so the user only fills in what differs from a bog-standard residential
/// buy-to-let.
library;

import 'package:flutter/material.dart';

import '../../api/api_client.dart';
import '../../theme/tokens.dart';

const List<String> kMortgageTypes = <String>[
  'none',
  'interest_only',
  'repayment',
];

const List<String> kFinanceCostClassifications = <String>[
  'residential',
  'non_residential',
];

class PropertyForm extends StatefulWidget {
  const PropertyForm({
    required this.api,
    required this.onDone,
    required this.onCancel,
    super.key,
  });

  final ApiClient api;
  final VoidCallback onDone;
  final VoidCallback onCancel;

  @override
  State<PropertyForm> createState() => _PropertyFormState();
}

class _PropertyFormState extends State<PropertyForm> {
  final TextEditingController _address1 = TextEditingController();
  final TextEditingController _address2 = TextEditingController();
  final TextEditingController _city = TextEditingController();
  final TextEditingController _postcode = TextEditingController();
  String _mortgageType = 'none';
  String _financeClassification = 'residential';
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _address1.dispose();
    _address2.dispose();
    _city.dispose();
    _postcode.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_address1.text.trim().isEmpty ||
        _city.text.trim().isEmpty ||
        _postcode.text.trim().isEmpty) {
      setState(() => _error = 'Address line 1, city and postcode are required.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.createProperty(
        addressLine1: _address1.text.trim(),
        addressLine2: _address2.text.trim(),
        city: _city.text.trim(),
        postcode: _postcode.text.trim(),
        financeCostClassification: _financeClassification,
        mortgageType: _mortgageType,
      );
      if (!mounted) return;
      widget.onDone();
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = '$error';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;

    return Container(
      margin: const EdgeInsets.fromLTRB(
        Spacing.lg,
        Spacing.md,
        Spacing.lg,
        Spacing.sm,
      ),
      padding: const EdgeInsets.all(Spacing.lg),
      decoration: BoxDecoration(
        color: palette.bgSurface,
        border: Border.all(color: palette.rule),
        borderRadius: Radii.mdRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('Add a property', style: text.titleMedium),
          const SizedBox(height: Spacing.md),
          _Field(
            label: 'Address line 1',
            child: Align(
              alignment: Alignment.centerLeft,
              child: TextField(
                controller: _address1,
                style: AppType.body.copyWith(color: palette.textBody),
                decoration: const InputDecoration(
                  isDense: true,
                  border: InputBorder.none,
                  hintText: '12 Liverpool Road',
                ),
              ),
            ),
          ),
          const SizedBox(height: Spacing.sm),
          _Field(
            label: 'Address line 2 (optional)',
            child: Align(
              alignment: Alignment.centerLeft,
              child: TextField(
                controller: _address2,
                style: AppType.body.copyWith(color: palette.textBody),
                decoration: const InputDecoration(
                  isDense: true,
                  border: InputBorder.none,
                ),
              ),
            ),
          ),
          const SizedBox(height: Spacing.sm),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                flex: 2,
                child: _Field(
                  label: 'City',
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: TextField(
                      controller: _city,
                      style: AppType.body.copyWith(color: palette.textBody),
                      decoration: const InputDecoration(
                        isDense: true,
                        border: InputBorder.none,
                        hintText: 'London',
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: Spacing.sm),
              Expanded(
                child: _Field(
                  label: 'Postcode',
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: TextField(
                      controller: _postcode,
                      style: AppType.body.copyWith(color: palette.textBody),
                      decoration: const InputDecoration(
                        isDense: true,
                        border: InputBorder.none,
                        hintText: 'N1 1AA',
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: Spacing.sm),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                child: _Field(
                  label: 'Mortgage',
                  child: DropdownButton<String>(
                    value: _mortgageType,
                    underline: const SizedBox.shrink(),
                    isExpanded: true,
                    items: <DropdownMenuItem<String>>[
                      for (final String t in kMortgageTypes)
                        DropdownMenuItem<String>(
                          value: t,
                          child: Text(_mortgageLabel(t)),
                        ),
                    ],
                    onChanged: (String? v) =>
                        setState(() => _mortgageType = v ?? _mortgageType),
                  ),
                ),
              ),
              const SizedBox(width: Spacing.sm),
              Expanded(
                child: _Field(
                  label: 'Type',
                  child: DropdownButton<String>(
                    value: _financeClassification,
                    underline: const SizedBox.shrink(),
                    isExpanded: true,
                    items: <DropdownMenuItem<String>>[
                      for (final String c in kFinanceCostClassifications)
                        DropdownMenuItem<String>(
                          value: c,
                          child: Text(_classificationLabel(c)),
                        ),
                    ],
                    onChanged: (String? v) => setState(
                      () => _financeClassification = v ?? _financeClassification,
                    ),
                  ),
                ),
              ),
            ],
          ),
          if (_error != null) ...<Widget>[
            const SizedBox(height: Spacing.sm),
            Text(
              '$_error.',
              style: AppType.body.copyWith(color: palette.danger),
            ),
          ],
          const SizedBox(height: Spacing.md),
          Row(
            children: <Widget>[
              FilledButton(
                onPressed: _busy ? null : _save,
                child: Text(_busy ? 'Saving' : 'Save'),
              ),
              const SizedBox(width: Spacing.sm),
              TextButton(
                onPressed: _busy ? null : widget.onCancel,
                child: const Text('Cancel'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

String _mortgageLabel(String t) => switch (t) {
  'interest_only' => 'Interest Only',
  'repayment' => 'Repayment (Interest + Capital)',
  _ => 'No Mortgage',
};

String _classificationLabel(String c) => c == 'residential'
    ? 'Residential'
    : 'Non-Residential';

class _Field extends StatelessWidget {
  const _Field({required this.label, required this.child});

  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(label, style: AppType.meta.copyWith(color: palette.textMuted)),
        const SizedBox(height: Spacing.xs),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: Spacing.sm),
          decoration: BoxDecoration(
            color: palette.bgRaised,
            border: Border.all(color: palette.ruleStrong),
            borderRadius: Radii.smRadius,
          ),
          child: child,
        ),
      ],
    );
  }
}
