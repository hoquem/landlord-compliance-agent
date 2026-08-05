/// What is waiting on you, and when it is due.
///
/// **Not a card grid.** The plan asked for cards; `DESIGN.md` bans identical
/// card grids and the hero-metric template outright, and "generic SaaS
/// dashboard" is a named anti-reference. Three boxes with big numbers would
/// be exactly that, and would answer a question nobody has — this product
/// has no vanity metric worth 48pt.
///
/// So the dashboard is a short list of statements, each one a thing to do
/// with somewhere to go. It reads as prose because that is what the
/// information is: *47 lines need a decision. Q2 is due on 7 November.*
///
/// **The deadline is rendered, never computed.** It arrives from
/// `GET /dashboard`, which calls `core/quarters.py`. A second implementation
/// here would be a second opinion about a statutory date.
library;

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../api/api_client.dart';
import '../../api/models.dart';
import '../../app/widgets/screen_scaffold.dart';
import '../../theme/status_colors.dart';
import '../../theme/tokens.dart';
import 'export_panel.dart';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({required this.api, super.key});

  final ApiClient api;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  DashboardSummary? _summary;
  String? _error;
  bool _exporting = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final DashboardSummary summary = await widget.api.getDashboard();
      if (!mounted) return;
      setState(() {
        _summary = summary;
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
    final DashboardSummary? s = _summary;

    return ScreenScaffold(
      title: 'Dashboard',
      action: FilledButton(
        onPressed: () => setState(() => _exporting = !_exporting),
        child: const Text('Export a quarter'),
      ),
      child: ListView(
        padding: const EdgeInsets.symmetric(
          horizontal: Spacing.xl,
          vertical: Spacing.xl,
        ),
        children: <Widget>[
          if (_exporting) ...<Widget>[
            ExportPanel(
              api: widget.api,
              onClose: () => setState(() => _exporting = false),
            ),
            const SizedBox(height: Spacing.xl),
          ],
          if (_error != null)
            Text(
              '$_error.',
              style: Theme.of(
                context,
              ).textTheme.bodyMedium?.copyWith(color: palette.danger),
            ),
          if (s != null) ..._statements(context, s),
        ],
      ),
    );
  }

  List<Widget> _statements(BuildContext context, DashboardSummary s) {
    return <Widget>[
      _Line(
        text: _deadlineSentence(s),
        // The deadline is information, not a task, unless it is close.
        state: s.daysUntilDeadline <= 14
            ? WorkState.needsYou
            : WorkState.settled,
      ),
      if (s.needsDecision > 0)
        _Line(
          text: s.needsDecision == 1
              ? '1 line needs a decision.'
              : '${s.needsDecision} lines need a decision.',
          state: WorkState.needsYou,
          goLabel: 'Review',
          onGo: () => context.go('/review'),
        ),
      // Two lines, not one. A file that could not be read is bad input and
      // wants a different export from the bank; a file that was read and
      // then could not be categorised is our failure, and its data is fine
      // and waiting. Merged, they read as "2 imports could not be read" —
      // half untrue, and it buried the actionable one.
      if (s.unreadableImports > 0)
        _Line(
          text: s.unreadableImports == 1
              ? '1 import could not be read.'
              : '${s.unreadableImports} imports could not be read.',
          state: WorkState.wrong,
          goLabel: 'Imports',
          onGo: () => context.go('/imports'),
        ),
      if (s.uncategorisedImports > 0)
        _Line(
          text: s.uncategorisedImports == 1
              ? '1 import was read but could not be categorised.'
              : '${s.uncategorisedImports} imports were read but could not be '
                    'categorised.',
          state: WorkState.wrong,
          goLabel: 'Imports',
          onGo: () => context.go('/imports'),
        ),
      if (s.expiredCertificates > 0)
        _Line(
          text: s.expiredCertificates == 1
              ? '1 certificate has lapsed.'
              : '${s.expiredCertificates} certificates have lapsed.',
          state: WorkState.wrong,
          goLabel: 'Certificates',
          onGo: () => context.go('/certificates'),
        ),
      if (s.expiringCertificates > 0)
        _Line(
          text: s.expiringCertificates == 1
              ? '1 certificate lapses within 60 days.'
              : '${s.expiringCertificates} certificates lapse within 60 days.',
          state: WorkState.needsYou,
          goLabel: 'Certificates',
          onGo: () => context.go('/certificates'),
        ),
      if (s.allClear)
        const _Line(
          text: 'Nothing else is waiting on you.',
          state: WorkState.settled,
        ),
    ];
  }

  static String _deadlineSentence(DashboardSummary s) {
    const List<String> months = <String>[
      'January', 'February', 'March', 'April', 'May', 'June',
      'July', 'August', 'September', 'October', 'November', 'December',
    ];
    final String when =
        '${s.nextDeadline.day} ${months[s.nextDeadline.month - 1]}';
    if (s.daysUntilDeadline < 0) return 'Your last update was due on $when.';
    if (s.daysUntilDeadline == 0) return 'Your next update is due today.';
    if (s.daysUntilDeadline == 1) return 'Your next update is due tomorrow, $when.';
    return 'Your next update is due on $when, in ${s.daysUntilDeadline} days.';
  }
}

/// One statement, with somewhere to go if there is something to do.
class _Line extends StatelessWidget {
  const _Line({
    required this.text,
    required this.state,
    this.goLabel,
    this.onGo,
  });

  final String text;
  final WorkState state;
  final String? goLabel;
  final VoidCallback? onGo;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final StatusColors colors = Theme.of(context).extension<StatusColors>()!;

    return Padding(
      padding: const EdgeInsets.only(bottom: Spacing.lg),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: <Widget>[
          // No leading stripe. A coloured bar down the left of a list item is
          // a side-stripe border, which DESIGN.md bans outright -- and the
          // first draft of this widget had one, with a comment explaining
          // why it was different. It wasn't.
          //
          // The state is carried by the sentence's own colour instead:
          // settled recedes to muted, wrong takes danger, anything asking
          // for a decision stays at full contrast. Nothing is added to the
          // screen to say what the text can say by itself.
          Expanded(
            child: Text(
              text,
              style: AppType.title.copyWith(
                color: switch (state) {
                  WorkState.settled => palette.textMuted,
                  WorkState.wrong => colors.wrong,
                  _ => palette.textHigh,
                },
              ),
            ),
          ),
          if (goLabel != null)
            TextButton(onPressed: onGo, child: Text(goLabel!)),
        ],
      ),
    );
  }
}
