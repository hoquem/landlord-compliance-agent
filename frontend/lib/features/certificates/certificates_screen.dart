/// Compliance certificates, grouped by the property they belong to.
///
/// **Status is derived on every read, never stored** — a cached "compliant"
/// flag goes stale exactly when it matters, on the day something lapses. The
/// API recomputes it per request and this screen only renders it.
///
/// Only properties that *have* certificates appear, because that is what the
/// endpoint returns and why: nothing in the system records which types a
/// property requires, so an empty group could not answer the question that
/// would justify showing it. That gap is real and named here rather than
/// papered over with a row that says "0 certificates".
library;

import 'package:flutter/material.dart';

import '../../api/api_client.dart';
import '../../api/models.dart';
import '../../api/money.dart';
import '../../app/widgets/screen_scaffold.dart';
import '../../app/widgets/status_pill.dart';
import '../../theme/tokens.dart';
import 'certificate_form.dart';

class CertificatesScreen extends StatefulWidget {
  const CertificatesScreen({required this.api, super.key});

  final ApiClient api;

  @override
  State<CertificatesScreen> createState() => _CertificatesScreenState();
}

class _CertificatesScreenState extends State<CertificatesScreen> {
  List<PropertyCertificates>? _groups;
  List<PropertyRef> _properties = <PropertyRef>[];
  String? _error;
  bool _adding = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final List<PropertyCertificates> groups =
          await widget.api.listCertificates();
      final List<PropertyRef> properties = await widget.api.listProperties();
      if (!mounted) return;
      setState(() {
        _groups = groups;
        _properties = properties;
        _error = null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '$error');
    }
  }

  String _propertyLabel(String id) {
    for (final PropertyRef p in _properties) {
      if (p.id == id) return p.label;
    }
    return id;
  }

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final List<PropertyCertificates> groups =
        _groups ?? <PropertyCertificates>[];

    return ScreenScaffold(
      title: 'Certificates',
      subtitle: 'Gas safety, EICR, EPC and licences, by property.',
      action: FilledButton(
        onPressed: () => setState(() => _adding = true),
        child: const Text('Add certificate'),
      ),
      child: ListView(
        padding: const EdgeInsets.only(bottom: Spacing.xl),
        children: <Widget>[
          if (_adding)
            CertificateForm(
              api: widget.api,
              properties: _properties,
              onDone: () {
                setState(() => _adding = false);
                _load();
              },
              onCancel: () => setState(() => _adding = false),
            ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(Spacing.xl),
              child: Text(
                '$_error.',
                style: Theme.of(
                  context,
                ).textTheme.bodyMedium?.copyWith(color: palette.danger),
              ),
            ),
          if (_groups != null && groups.isEmpty && !_adding)
            const _NoCertificates(),
          for (final PropertyCertificates group in groups)
            _PropertyGroup(
              label: _propertyLabel(group.propertyId),
              certificates: group.certificates,
              onDelete: (String id) async {
                await widget.api.deleteCertificate(id);
                await _load();
              },
            ),
        ],
      ),
    );
  }
}

class _PropertyGroup extends StatelessWidget {
  const _PropertyGroup({
    required this.label,
    required this.certificates,
    required this.onDelete,
  });

  final String label;
  final List<Certificate> certificates;
  final Future<void> Function(String id) onDelete;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.fromLTRB(
            Spacing.xl,
            Spacing.lg,
            Spacing.xl,
            Spacing.sm,
          ),
          child: Text(label, style: text.titleMedium),
        ),
        for (final Certificate c in certificates)
          Column(
            children: <Widget>[
              Divider(height: 1, thickness: 1, color: palette.rule),
              Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: Spacing.xl,
                  vertical: Spacing.sm + Spacing.xs,
                ),
                child: Row(
                  children: <Widget>[
                    Expanded(
                      child: Text(
                        categoryLabel(c.certificateType),
                        style: AppType.body.copyWith(color: palette.textHigh),
                      ),
                    ),
                    Text(
                      _expiry(c.expiryDate),
                      style: AppType.numeric.copyWith(
                        color: palette.textMuted,
                      ),
                    ),
                    const SizedBox(width: Spacing.md),
                    SizedBox(
                      width: 88,
                      child: Align(
                        alignment: Alignment.centerRight,
                        child: StatusPill(state: c.state, label: c.label),
                      ),
                    ),
                    const SizedBox(width: Spacing.sm),
                    IconButton(
                      tooltip: 'Remove',
                      iconSize: 18,
                      icon: const Icon(Icons.close),
                      onPressed: () async {
                        final bool? confirm = await showDialog<bool>(
                          context: context,
                          builder: (BuildContext ctx) => AlertDialog(
                            title: const Text('Remove certificate?'),
                            content: Text(
                              'Remove ${categoryLabel(c.certificateType)} '
                              'expiring ${_expiry(c.expiryDate)}?\n\n'
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
                                child: const Text('Remove'),
                              ),
                            ],
                          ),
                        );
                        if (confirm == true) {
                          await onDelete(c.id);
                        }
                      },
                    ),
                  ],
                ),
              ),
            ],
          ),
      ],
    );
  }

  static String _expiry(DateTime d) {
    const List<String> months = <String>[
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    return '${d.day} ${months[d.month - 1]} ${d.year}';
  }
}

class _NoCertificates extends StatelessWidget {
  const _NoCertificates();

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.all(Spacing.xl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('No certificates recorded.', style: text.titleMedium),
          const SizedBox(height: Spacing.xs),
          Text(
            'Add gas safety, EICR and EPC dates and this page will tell you '
            'what lapses next.',
            style: text.bodyMedium?.copyWith(color: palette.textMuted),
          ),
        ],
      ),
    );
  }
}
