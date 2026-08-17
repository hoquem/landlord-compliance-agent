/// What is waiting on you, and when it is due.
///
/// Redesigned as a property investor dashboard:
///   - Deadline countdown hero
///   - Action items with status indicators
///   - Quick metrics (review queue, compliance, imports)
///   - Export panel inline
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
    final StatusColors colors =
        Theme.of(context).extension<StatusColors>()!;
    final DashboardSummary? s = _summary;

    return ScreenScaffold(
      title: 'Dashboard',
      action: FilledButton.icon(
        onPressed: () => setState(() => _exporting = !_exporting),
        icon: const Icon(Icons.file_download_outlined, size: 18),
        label: const Text('Export'),
      ),
      child: ListView(
        padding: const EdgeInsets.symmetric(
          horizontal: Spacing.lg,
          vertical: Spacing.sm,
        ),
        children: <Widget>[
          if (_exporting) ...<Widget>[
            ExportPanel(
              api: widget.api,
              onClose: () => setState(() => _exporting = false),
            ),
            const SizedBox(height: Spacing.md),
          ],
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: Spacing.sm),
              child: SelectableText(
                '$_error.',
                style: AppType.body.copyWith(color: palette.danger),
              ),
            ),
          if (s != null) ...<Widget>[
            // ── Deadline Hero ──
            _DeadlineHero(summary: s, palette: palette, colors: colors),
            const SizedBox(height: Spacing.md),

            // ── Metric Grid ──
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                _MetricCard(
                  label: 'Needs Decision',
                  value: s.needsDecision,
                  icon: Icons.pending_actions_outlined,
                  color: s.needsDecision > 0 ? colors.needsYou : palette.textMuted,
                  palette: palette,
                  onTap: s.needsDecision > 0
                      ? () => context.go('/review')
                      : null,
                ),
                const SizedBox(width: Spacing.sm),
                _MetricCard(
                  label: 'Failed Imports',
                  value: s.unreadableImports + s.uncategorisedImports,
                  icon: Icons.error_outline,
                  color: (s.unreadableImports + s.uncategorisedImports) > 0
                      ? colors.wrong
                      : palette.textMuted,
                  palette: palette,
                  onTap: (s.unreadableImports + s.uncategorisedImports) > 0
                      ? () => context.go('/imports')
                      : null,
                ),
                const SizedBox(width: Spacing.sm),
                _MetricCard(
                  label: 'Expired Certs',
                  value: s.expiredCertificates,
                  icon: Icons.warning_amber_outlined,
                  color: s.expiredCertificates > 0
                      ? colors.wrong
                      : palette.textMuted,
                  palette: palette,
                  onTap: s.expiredCertificates > 0
                      ? () => context.go('/certificates')
                      : null,
                ),
                const SizedBox(width: Spacing.sm),
                _MetricCard(
                  label: 'Expiring Certs',
                  value: s.expiringCertificates,
                  icon: Icons.schedule_outlined,
                  color: s.expiringCertificates > 0
                      ? colors.needsYou
                      : palette.textMuted,
                  palette: palette,
                  onTap: s.expiringCertificates > 0
                      ? () => context.go('/certificates')
                      : null,
                ),
              ],
            ),
            const SizedBox(height: Spacing.md),

            // ── Action Items ──
            if (_hasActions(s)) ...<Widget>[
              Text(
                'ACTION REQUIRED',
                style: AppType.label.copyWith(
                  color: palette.textMuted,
                  letterSpacing: 1.2,
                  fontSize: 11,
                ),
              ),
              const SizedBox(height: Spacing.xs),
              ..._actionItems(context, s, palette, colors),
            ],

            // ── All Clear ──
            if (s.allClear)
              Container(
                padding: const EdgeInsets.all(Spacing.lg),
                decoration: BoxDecoration(
                  color: palette.bgSurface,
                  borderRadius: Radii.mdRadius,
                  border: Border.all(color: palette.rule),
                ),
                child: Row(
                  children: <Widget>[
                    Icon(
                      Icons.check_circle_outline,
                      color: palette.accent,
                      size: 24,
                    ),
                    const SizedBox(width: Spacing.sm),
                    Expanded(
                      child: Text(
                        'Everything is up to date. Nothing is waiting on you.',
                        style: AppType.body.copyWith(
                          color: palette.textHigh,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }

  bool _hasActions(DashboardSummary s) =>
      s.needsDecision > 0 ||
      s.unreadableImports > 0 ||
      s.uncategorisedImports > 0 ||
      s.expiredCertificates > 0 ||
      s.expiringCertificates > 0;

  List<Widget> _actionItems(
    BuildContext context,
    DashboardSummary s,
    Palette palette,
    StatusColors colors,
  ) {
    final List<Widget> items = <Widget>[];

    if (s.needsDecision > 0) {
      items.add(_ActionRow(
        icon: Icons.pending_actions_outlined,
        iconColor: colors.needsYou,
        title: s.needsDecision == 1
            ? '1 transaction needs review'
            : '${s.needsDecision} transactions need review',
        subtitle: 'Categorise and confirm to unblock exports',
        palette: palette,
        onTap: () => context.go('/review'),
      ));
    }

    if (s.unreadableImports > 0) {
      items.add(_ActionRow(
        icon: Icons.broken_image_outlined,
        iconColor: colors.wrong,
        title: s.unreadableImports == 1
            ? '1 import could not be read'
            : '${s.unreadableImports} imports could not be read',
        subtitle: 'Bad input — try a different export format',
        palette: palette,
        onTap: () => context.go('/imports'),
      ));
    }

    if (s.uncategorisedImports > 0) {
      items.add(_ActionRow(
        icon: Icons.psychology_outlined,
        iconColor: colors.wrong,
        title: s.uncategorisedImports == 1
            ? '1 import could not be categorised'
            : '${s.uncategorisedImports} imports could not be categorised',
        subtitle: 'Data was read but AI categorisation failed',
        palette: palette,
        onTap: () => context.go('/imports'),
      ));
    }

    if (s.expiredCertificates > 0) {
      items.add(_ActionRow(
        icon: Icons.dangerous_outlined,
        iconColor: colors.wrong,
        title: s.expiredCertificates == 1
            ? '1 certificate has expired'
            : '${s.expiredCertificates} certificates have expired',
        subtitle: 'Properties without valid compliance certificates',
        palette: palette,
        onTap: () => context.go('/certificates'),
      ));
    }

    if (s.expiringCertificates > 0) {
      items.add(_ActionRow(
        icon: Icons.schedule_outlined,
        iconColor: colors.needsYou,
        title: s.expiringCertificates == 1
            ? '1 certificate expires within 60 days'
            : '${s.expiringCertificates} certificates expire within 60 days',
        subtitle: 'Renew before they lapse',
        palette: palette,
        onTap: () => context.go('/certificates'),
      ));
    }

    return items;
  }
}

// ── Deadline Hero ─────────────────────────────────────────────
class _DeadlineHero extends StatelessWidget {
  const _DeadlineHero({
    required this.summary,
    required this.palette,
    required this.colors,
  });

  final DashboardSummary summary;
  final Palette palette;
  final StatusColors colors;

  @override
  Widget build(BuildContext context) {
    final bool urgent = summary.daysUntilDeadline <= 14;
    final bool overdue = summary.daysUntilDeadline < 0;
    final Color accentColor =
        overdue ? colors.wrong : (urgent ? colors.needsYou : palette.accent);

    return Container(
      padding: const EdgeInsets.all(Spacing.lg),
      decoration: BoxDecoration(
        color: palette.bgSurface,
        borderRadius: Radii.mdRadius,
        border: Border.all(color: palette.rule),
      ),
      child: Row(
        children: <Widget>[
          // Days remaining — big number
          Container(
            width: 80,
            height: 80,
            decoration: BoxDecoration(
              color: accentColor.withValues(alpha: 0.12),
              borderRadius: Radii.mdRadius,
            ),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: <Widget>[
                Text(
                  overdue ? '!' : '${summary.daysUntilDeadline}',
                  style: AppType.title.copyWith(
                    color: accentColor,
                    fontSize: overdue ? 36 : 32,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                Text(
                  overdue ? 'OVERDUE' : 'DAYS',
                  style: AppType.meta.copyWith(
                    color: accentColor.withValues(alpha: 0.7),
                    fontSize: 10,
                    letterSpacing: 1,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: Spacing.md),
          // Deadline text
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  overdue
                      ? 'Your last update was overdue'
                      : summary.daysUntilDeadline == 0
                          ? 'Your next update is due today'
                          : summary.daysUntilDeadline == 1
                              ? 'Due tomorrow'
                              : 'Next quarterly update',
                  style: AppType.title.copyWith(
                    color: palette.textHigh,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  _deadlineDate(summary),
                  style: AppType.body.copyWith(color: palette.textMuted),
                ),
                const SizedBox(height: Spacing.xs),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: Spacing.sm,
                    vertical: 2,
                  ),
                  decoration: BoxDecoration(
                    color: accentColor.withValues(alpha: 0.12),
                    borderRadius: Radii.smRadius,
                  ),
                  child: Text(
                    urgent ? 'ACTION NEEDED' : 'ON TRACK',
                    style: AppType.meta.copyWith(
                      color: accentColor,
                      fontWeight: FontWeight.w600,
                      fontSize: 10,
                      letterSpacing: 0.8,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  static String _deadlineDate(DashboardSummary s) {
    const List<String> months = <String>[
      'January', 'February', 'March', 'April', 'May', 'June',
      'July', 'August', 'September', 'October', 'November', 'December',
    ];
    return '${s.nextDeadline.day} ${months[s.nextDeadline.month - 1]} ${s.nextDeadline.year}';
  }
}

// ── Metric Card ───────────────────────────────────────────────
class _MetricCard extends StatelessWidget {
  const _MetricCard({
    required this.label,
    required this.value,
    required this.icon,
    required this.color,
    required this.palette,
    this.onTap,
  });

  final String label;
  final int value;
  final IconData icon;
  final Color color;
  final Palette palette;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final bool actionable = onTap != null;
    final bool isZero = value == 0;

    return Expanded(
      child: InkWell(
        onTap: onTap,
        borderRadius: Radii.smRadius,
        child: Container(
          padding: const EdgeInsets.symmetric(
            horizontal: Spacing.sm,
            vertical: Spacing.sm,
          ),
          decoration: BoxDecoration(
            color: palette.bgSurface,
            borderRadius: Radii.smRadius,
            border: Border.all(
              color: actionable ? color.withValues(alpha: 0.3) : palette.rule,
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Icon(icon, size: 14, color: color),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      label,
                      style: AppType.meta.copyWith(
                        color: palette.textMuted,
                        fontSize: 10,
                        letterSpacing: 0.5,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                '$value',
                style: AppType.title.copyWith(
                  color: isZero ? palette.textMuted : color,
                  fontSize: 22,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Action Row ────────────────────────────────────────────────
class _ActionRow extends StatelessWidget {
  const _ActionRow({
    required this.icon,
    required this.iconColor,
    required this.title,
    required this.subtitle,
    required this.palette,
    this.onTap,
  });

  final IconData icon;
  final Color iconColor;
  final String title;
  final String subtitle;
  final Palette palette;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: Radii.smRadius,
      child: Container(
        padding: const EdgeInsets.symmetric(
          horizontal: Spacing.md,
          vertical: Spacing.sm,
        ),
        margin: const EdgeInsets.only(bottom: Spacing.xs),
        decoration: BoxDecoration(
          color: palette.bgSurface,
          borderRadius: Radii.smRadius,
          border: Border.all(color: palette.rule),
        ),
        child: Row(
          children: <Widget>[
            Icon(icon, size: 20, color: iconColor),
            const SizedBox(width: Spacing.sm),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  SelectableText(
                    title,
                    style: AppType.body.copyWith(
                      color: palette.textHigh,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  Text(
                    subtitle,
                    style: AppType.meta.copyWith(
                      color: palette.textMuted,
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
            ),
            if (onTap != null)
              Icon(
                Icons.chevron_right,
                size: 18,
                color: palette.textMuted,
              ),
          ],
        ),
      ),
    );
  }
}