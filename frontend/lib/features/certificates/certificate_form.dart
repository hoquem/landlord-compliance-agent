/// Recording one certificate. Inline, like every other form here.
///
/// The expiry date is required and the issue date is not: a certificate
/// without an expiry cannot answer the only question worth asking of it,
/// while the issue date is often unknown for inherited paperwork
/// (`docs/domain/compliance.md`).
library;

import 'package:flutter/material.dart';

import '../../api/api_client.dart';
import '../../api/models.dart';
import '../../api/money.dart';
import '../../theme/tokens.dart';

/// The five types, matching the SQL enum and `core/certificates.py`.
///
/// Unlike banks and HMRC categories this list is not fetched: it has no
/// endpoint, and adding one for five values that change roughly never was
/// not worth the round trip. The API rejects an unknown type with a 422, so
/// the failure is loud rather than silent — but this *is* a second copy, and
/// that is the reason it is small enough to see.
const List<String> kCertificateTypes = <String>[
  'gas_safety',
  'eicr',
  'epc',
  'hmo_licence',
  'selective_licence',
];

class CertificateForm extends StatefulWidget {
  const CertificateForm({
    required this.api,
    required this.properties,
    required this.onDone,
    required this.onCancel,
    super.key,
  });

  final ApiClient api;
  final List<PropertyRef> properties;
  final VoidCallback onDone;
  final VoidCallback onCancel;

  @override
  State<CertificateForm> createState() => _CertificateFormState();
}

class _CertificateFormState extends State<CertificateForm> {
  String? _propertyId;
  String _type = kCertificateTypes.first;
  DateTime? _expiry;
  final TextEditingController _ref = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _ref.dispose();
    super.dispose();
  }

  Future<void> _pickExpiry() async {
    final DateTime now = DateTime.now();
    final DateTime? picked = await showDatePicker(
      context: context,
      initialDate: _expiry ?? now,
      // Past dates allowed: recording an already-lapsed certificate is
      // exactly how someone finds out they have one.
      firstDate: DateTime(now.year - 10),
      lastDate: DateTime(now.year + 20),
    );
    if (picked == null || !mounted) return;
    setState(() => _expiry = picked);
  }

  Future<void> _save() async {
    final String? propertyId = _propertyId;
    final DateTime? expiry = _expiry;
    if (propertyId == null || expiry == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.createCertificate(
        propertyId: propertyId,
        certificateType: _type,
        expiryDate: expiry,
        certificateRef: _ref.text,
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
        Spacing.xl,
        Spacing.lg,
        Spacing.xl,
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
          Text('Add a certificate', style: text.titleMedium),
          const SizedBox(height: Spacing.lg),
          Wrap(
            spacing: Spacing.md,
            runSpacing: Spacing.md,
            children: <Widget>[
              _Field(
                label: 'Property',
                child: DropdownButton<String>(
                  value: _propertyId,
                  hint: const Text('Choose'),
                  underline: const SizedBox.shrink(),
                  items: <DropdownMenuItem<String>>[
                    for (final PropertyRef p in widget.properties)
                      DropdownMenuItem<String>(
                        value: p.id,
                        child: Text(p.label),
                      ),
                  ],
                  onChanged: (String? v) => setState(() => _propertyId = v),
                ),
              ),
              _Field(
                label: 'Type',
                child: DropdownButton<String>(
                  value: _type,
                  underline: const SizedBox.shrink(),
                  items: <DropdownMenuItem<String>>[
                    for (final String t in kCertificateTypes)
                      DropdownMenuItem<String>(
                        value: t,
                        child: Text(categoryLabel(t)),
                      ),
                  ],
                  onChanged: (String? v) => setState(() => _type = v ?? _type),
                ),
              ),
              _Field(
                label: 'Expires',
                child: TextButton(
                  onPressed: _pickExpiry,
                  child: Text(
                    _expiry == null
                        ? 'Choose a date'
                        : '${_expiry!.day}/${_expiry!.month}/${_expiry!.year}',
                  ),
                ),
              ),
              SizedBox(
                width: 200,
                child: TextField(
                  controller: _ref,
                  style: AppType.body.copyWith(color: palette.textBody),
                  decoration: const InputDecoration(
                    labelText: 'Reference (optional)',
                    isDense: true,
                  ),
                ),
              ),
            ],
          ),
          if (_error != null) ...<Widget>[
            const SizedBox(height: Spacing.md),
            Text(
              '$_error.',
              style: text.bodyMedium?.copyWith(color: palette.danger),
            ),
          ],
          const SizedBox(height: Spacing.lg),
          Row(
            children: <Widget>[
              FilledButton(
                onPressed: _propertyId == null || _expiry == null || _busy
                    ? null
                    : _save,
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
