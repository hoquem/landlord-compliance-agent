/// Creating an entity. Inline, like every other form here.
///
/// An entity is an ownership vehicle (a company or a natural person), and
/// its tax regime is the single decision that shapes its quarterly export.
library;

import 'package:flutter/material.dart';

import '../../api/api_client.dart';
import '../../theme/tokens.dart';

const List<String> kTaxRegimes = <String>['mtd_itsa', 'ct'];

class EntityForm extends StatefulWidget {
  const EntityForm({
    required this.api,
    required this.onDone,
    required this.onCancel,
    super.key,
  });

  final ApiClient api;
  final VoidCallback onDone;
  final VoidCallback onCancel;

  @override
  State<EntityForm> createState() => _EntityFormState();
}

class _EntityFormState extends State<EntityForm> {
  final TextEditingController _name = TextEditingController();
  String _taxRegime = 'mtd_itsa';
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_name.text.trim().isEmpty) {
      setState(() => _error = 'A name is required.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.createEntity(
        name: _name.text.trim(),
        taxRegime: _taxRegime,
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
          Text('Add an entity', style: text.titleMedium),
          const SizedBox(height: Spacing.md),
          _Field(
            label: 'Name',
            child: Align(
              alignment: Alignment.centerLeft,
              child: TextField(
                controller: _name,
                style: AppType.body.copyWith(color: palette.textBody),
                decoration: const InputDecoration(
                  isDense: true,
                  border: InputBorder.none,
                  hintText: 'e.g. Roshina Properties Ltd',
                ),
              ),
            ),
          ),
          const SizedBox(height: Spacing.sm),
          _Field(
            label: 'Tax regime',
            child: DropdownButton<String>(
              value: _taxRegime,
              underline: const SizedBox.shrink(),
              isExpanded: true,
              items: <DropdownMenuItem<String>>[
                for (final String t in kTaxRegimes)
                  DropdownMenuItem<String>(
                    value: t,
                    child: Text(t == 'mtd_itsa' ? 'MTD ITSA' : 'Corporation Tax'),
                  ),
              ],
              onChanged: (String? v) =>
                  setState(() => _taxRegime = v ?? _taxRegime),
            ),
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
