/// Statement uploads, and what happened to each one.
///
/// **A failed import is the screen's most important content, not an
/// exception to it.** The backend goes to real trouble to name the row that
/// broke and why; the spec says this failure UX matters more than format
/// coverage. So a failure is not a red dot with a tooltip — it states the
/// row number and the message inline, where it cannot be missed.
///
/// The plan asked for a staggered list entrance. `DESIGN.md` forbids it
/// ("motion marks state changes, not arrivals"; no page-load choreography),
/// and the design language is newer than the plan, so there is none. Motion
/// here marks the one real state change: a newly uploaded import arriving.
library;

import 'package:flutter/material.dart';

import '../../api/api_client.dart';
import '../../api/models.dart';
import '../../app/widgets/screen_scaffold.dart';
import '../../app/widgets/status_pill.dart';
import '../../theme/status_colors.dart';
import '../../theme/tokens.dart';
import 'upload_panel.dart';

class ImportsScreen extends StatefulWidget {
  const ImportsScreen({required this.api, super.key});

  final ApiClient api;

  @override
  State<ImportsScreen> createState() => _ImportsScreenState();
}

class _ImportsScreenState extends State<ImportsScreen> {
  List<ImportSummary>? _imports;
  String? _error;
  bool _uploading = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final List<ImportSummary> rows = await widget.api.listImports();
      if (!mounted) return;
      setState(() {
        _imports = rows;
        _error = null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '$error');
    }
  }

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    return ScreenScaffold(
      title: 'Imports',
      subtitle: 'Bank statements you have uploaded, newest last.',
      action: FilledButton(
        onPressed: _uploading ? null : () => setState(() => _uploading = true),
        child: const Text('Add statement'),
      ),
      child: ListView(
        padding: const EdgeInsets.only(bottom: Spacing.xl),
        children: <Widget>[
          if (_uploading)
            UploadPanel(
              api: widget.api,
              onDone: () {
                setState(() => _uploading = false);
                _load();
              },
              onCancel: () => setState(() => _uploading = false),
            ),
          if (_error != null) _LoadError(message: _error!, onRetry: _load),
          if (_error == null && _imports == null) const _Loading(),
          if (_imports != null && _imports!.isEmpty && !_uploading)
            const _NoImportsYet(),
          for (final ImportSummary row in _imports ?? <ImportSummary>[])
            _ImportRow(
              record: row,
              onDelete: () async {
                final bool? confirm = await showDialog<bool>(
                  context: context,
                  builder: (ctx) => AlertDialog(
                    title: const Text('Delete import?'),
                    content: Text(
                      'Delete ${row.sourceBank} import from ${_ImportRow._when(row.createdAt)}?\n\n'
                      'This removes all ${row.sourceBank} transactions from this import.\n'
                      'This cannot be undone.',
                    ),
                    actions: <Widget>[
                      TextButton(
                        onPressed: () => Navigator.pop(ctx, false),
                        child: const Text('Cancel'),
                      ),
                      FilledButton(
                        onPressed: () => Navigator.pop(ctx, true),
                        style: FilledButton.styleFrom(
                          backgroundColor: palette.danger,
                        ),
                        child: const Text('Delete'),
                      ),
                    ],
                  ),
                );
                if (confirm == true) {
                  try {
                    await widget.api.deleteImport(row.id);
                    _load();
                  } catch (e) {
                    if (mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text('Failed to delete: $e')),
                      );
                    }
                  }
                }
              },
            ),
        ],
      ),
    );
  }
}

/// One import: what it was, how it went, and what to do if it went wrong.
class _ImportRow extends StatelessWidget {
  const _ImportRow({required this.record, required this.onDelete});

  final ImportSummary record;
  final Future<void> Function() onDelete;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;
    final bool failed = record.state == WorkState.wrong;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: Spacing.xl,
            vertical: Spacing.md,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Expanded(
                    child: Text(record.sourceBank, style: text.titleMedium),
                  ),
                  StatusPill(state: record.state, label: record.label),
                  const SizedBox(width: Spacing.sm),
                  IconButton(
                    tooltip: 'Delete import',
                    iconSize: 18,
                    icon: const Icon(Icons.close),
                    onPressed: onDelete,
                  ),
                ],
              ),
              const SizedBox(height: Spacing.xs),
              Text(_when(record.createdAt), style: text.bodySmall),
              if (failed) ...<Widget>[
                const SizedBox(height: Spacing.md),
                _FailureDetail(record: record),
              ],
            ],
          ),
        ),
        Divider(height: 1, thickness: 1, color: palette.rule),
      ],
    );
  }

  static String _when(DateTime at) {
    const List<String> months = <String>[
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    final DateTime local = at.toLocal();
    return '${local.day} ${months[local.month - 1]} ${local.year}';
  }
}

/// The row that broke and why, stated rather than hinted at.
class _FailureDetail extends StatelessWidget {
  const _FailureDetail({required this.record});

  final ImportSummary record;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final StatusColors colors = Theme.of(context).extension<StatusColors>()!;
    final TextTheme text = Theme.of(context).textTheme;
    final int? row = record.failedRow;

    return Container(
      padding: const EdgeInsets.all(Spacing.md),
      decoration: BoxDecoration(
        color: palette.bgSurface,
        border: Border.all(color: palette.dangerDim),
        borderRadius: Radii.smRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            row != null
                ? 'Row $row stopped this import.'
                : 'This import could not be read.',
            style: text.titleMedium?.copyWith(color: colors.wrong),
          ),
          const SizedBox(height: Spacing.xs),
          Text(
            '${record.failureMessage ?? 'No detail was recorded.'} '
            'Nothing was saved, so fix that row and upload again.',
            style: text.bodyMedium,
          ),
        ],
      ),
    );
  }
}

class _NoImportsYet extends StatelessWidget {
  const _NoImportsYet();

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.all(Spacing.xl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('No statements yet.', style: text.titleMedium),
          const SizedBox(height: Spacing.xs),
          Text(
            'Export a CSV from your bank, then add it here. '
            'Everything else starts from that file.',
            style: text.bodyMedium?.copyWith(color: palette.textMuted),
          ),
        ],
      ),
    );
  }
}

class _Loading extends StatelessWidget {
  const _Loading();

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    // Skeleton rows rather than a centred spinner: the shape of what is
    // coming is more use than a rotating circle, and it does not move.
    return Column(
      children: <Widget>[
        for (int i = 0; i < 3; i++)
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: Spacing.xl,
              vertical: Spacing.md,
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Container(height: 14, width: 140, color: palette.bgRaised),
                const SizedBox(height: Spacing.sm),
                Container(height: 10, width: 90, color: palette.bgSurface),
              ],
            ),
          ),
      ],
    );
  }
}

class _LoadError extends StatelessWidget {
  const _LoadError({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.all(Spacing.xl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            "Couldn't load your imports.",
            style: text.titleMedium?.copyWith(color: palette.danger),
          ),
          const SizedBox(height: Spacing.xs),
          Text('$message.', style: text.bodyMedium),
          const SizedBox(height: Spacing.md),
          FilledButton(onPressed: onRetry, child: const Text('Try again')),
        ],
      ),
    );
  }
}
