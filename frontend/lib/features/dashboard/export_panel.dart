/// Generating a quarter's export pack, and getting the files.
///
/// **A refusal here is the product working.** The backend declines to
/// produce figures it cannot stand behind: an unreviewed line blocks the
/// whole export, and a filed quarter whose history changed raises rather
/// than guessing. `PRODUCT.md` makes it a design principle that a refusal
/// must read as protection, not as an obstacle — so the message is shown in
/// full, with a way straight to the work that clears it.
library;

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../api/api_client.dart';
import '../../api/models.dart';
import '../../theme/tokens.dart';

class ExportPanel extends StatefulWidget {
  const ExportPanel({required this.api, required this.onClose, super.key});

  final ApiClient api;
  final VoidCallback onClose;

  @override
  State<ExportPanel> createState() => _ExportPanelState();
}

class _ExportPanelState extends State<ExportPanel> {
  List<Entity> _entities = <Entity>[];
  String? _entityId;
  int _taxYear = 2026;
  int _quarter = 1;
  bool _busy = false;
  String? _error;
  ExportResult? _result;

  @override
  void initState() {
    super.initState();
    _loadEntities();
  }

  Future<void> _loadEntities() async {
    try {
      final List<Entity> entities = await widget.api.listEntities();
      if (!mounted) return;
      setState(() {
        _entities = entities;
        _entityId ??= entities.isNotEmpty ? entities.first.id : null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '$error');
    }
  }

  Future<void> _export() async {
    final String? entityId = _entityId;
    if (entityId == null) return;
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final ExportResult result = await widget.api.exportQuarter(
        entityId: entityId,
        taxYear: _taxYear,
        quarter: _quarter,
      );
      if (!mounted) return;
      setState(() {
        _busy = false;
        _result = result;
      });
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
      padding: const EdgeInsets.all(Spacing.lg),
      decoration: BoxDecoration(
        color: palette.bgSurface,
        border: Border.all(color: palette.rule),
        borderRadius: Radii.mdRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('Export a quarter', style: text.titleMedium),
          const SizedBox(height: Spacing.lg),
          Wrap(
            spacing: Spacing.md,
            runSpacing: Spacing.md,
            children: <Widget>[
              _Field(
                label: 'Entity',
                child: DropdownButton<String>(
                  value: _entityId,
                  hint: const Text('Choose'),
                  underline: const SizedBox.shrink(),
                  items: <DropdownMenuItem<String>>[
                    for (final Entity e in _entities)
                      DropdownMenuItem<String>(value: e.id, child: Text(e.name)),
                  ],
                  onChanged: (String? v) => setState(() => _entityId = v),
                ),
              ),
              _Field(
                label: 'Tax year',
                child: DropdownButton<int>(
                  value: _taxYear,
                  underline: const SizedBox.shrink(),
                  items: <DropdownMenuItem<int>>[
                    for (int y = 2026; y <= 2030; y++)
                      DropdownMenuItem<int>(
                        value: y,
                        child: Text('$y-${(y + 1) % 100}'),
                      ),
                  ],
                  onChanged: (int? v) => setState(() => _taxYear = v ?? _taxYear),
                ),
              ),
              _Field(
                label: 'Quarter',
                child: DropdownButton<int>(
                  value: _quarter,
                  underline: const SizedBox.shrink(),
                  items: <DropdownMenuItem<int>>[
                    for (int q = 1; q <= 4; q++)
                      DropdownMenuItem<int>(value: q, child: Text('Q$q')),
                  ],
                  onChanged: (int? v) => setState(() => _quarter = v ?? _quarter),
                ),
              ),
            ],
          ),
          if (_error != null) ...<Widget>[
            const SizedBox(height: Spacing.lg),
            _Refusal(message: _error!),
          ],
          if (_result != null) ...<Widget>[
            const SizedBox(height: Spacing.lg),
            _Files(api: widget.api, result: _result!),
          ],
          const SizedBox(height: Spacing.lg),
          Row(
            children: <Widget>[
              FilledButton(
                onPressed: _entityId == null || _busy ? null : _export,
                child: Text(_busy ? 'Generating' : 'Generate'),
              ),
              const SizedBox(width: Spacing.sm),
              TextButton(
                onPressed: widget.onClose,
                child: const Text('Close'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// The backend declined, and said why. Shown in full.
class _Refusal extends StatelessWidget {
  const _Refusal({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;

    // "N transaction(s) in this period are not yet reviewed" is the common
    // case, and the fix is one click away, so offer it.
    final bool blocked = message.contains('not yet reviewed');

    return Container(
      padding: const EdgeInsets.all(Spacing.md),
      decoration: BoxDecoration(
        color: palette.bgRaised,
        border: Border.all(color: palette.dangerDim),
        borderRadius: Radii.smRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            'This quarter cannot be exported yet.',
            style: text.titleMedium?.copyWith(color: palette.danger),
          ),
          const SizedBox(height: Spacing.xs),
          Text(message, style: text.bodyMedium),
          if (blocked) ...<Widget>[
            const SizedBox(height: Spacing.sm),
            TextButton(
              onPressed: () => context.go('/review'),
              child: const Text('Go and review them'),
            ),
          ],
        ],
      ),
    );
  }
}

/// The generated files, each behind a short-lived signed URL.
class _Files extends StatelessWidget {
  const _Files({required this.api, required this.result});

  final ApiClient api;
  final ExportResult result;

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;
    final String version = result.version == null
        ? ''
        : ' · version ${result.version}';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(
          '${result.taxYear} ${result.quarter} is ready$version.',
          style: text.titleMedium,
        ),
        const SizedBox(height: Spacing.sm),
        for (final ExportDocument d in result.documents)
          TextButton(
            onPressed: () => api.downloadUrl(d.id),
            child: Text(d.label),
          ),
      ],
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
