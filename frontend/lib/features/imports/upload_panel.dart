/// The upload form, inline rather than in a modal.
///
/// `DESIGN.md` bans the modal-as-first-thought, and this is the case that
/// proves the rule useful: the form's whole job is to add a row to the list
/// behind it, and a modal would hide that list at the moment it matters.
///
/// Three things are required and none can be guessed. The entity decides
/// whose return the money lands on. The bank decides which parser reads the
/// file — `imports.source_bank` is NOT NULL precisely so the parser is told
/// rather than made to sniff, and the list comes from the backend so it
/// cannot drift from the registry. The file is the file.
library;

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import '../../api/api_client.dart';
import '../../api/models.dart';
import '../../theme/tokens.dart';

class UploadPanel extends StatefulWidget {
  const UploadPanel({
    required this.api,
    required this.onDone,
    required this.onCancel,
    super.key,
  });

  final ApiClient api;
  final VoidCallback onDone;
  final VoidCallback onCancel;

  @override
  State<UploadPanel> createState() => _UploadPanelState();
}

class _UploadPanelState extends State<UploadPanel> {
  List<Entity> _entities = <Entity>[];
  List<String> _banks = <String>[];
  String? _entityId;
  String? _bank;
  PlatformFile? _file;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadOptions();
  }

  Future<void> _loadOptions() async {
    try {
      final List<Entity> entities = await widget.api.listEntities();
      final List<String> banks = await widget.api.listBanks();
      if (!mounted) return;
      setState(() {
        _entities = entities;
        _banks = banks;
        _entityId ??= entities.isNotEmpty ? entities.first.id : null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '$error');
    }
  }

  Future<void> _pick() async {
    final FilePickerResult? result = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: <String>['csv'],
      withData: true,
    );
    if (result == null || !mounted) return;
    setState(() => _file = result.files.single);
  }

  Future<void> _upload() async {
    final PlatformFile? file = _file;
    final String? entityId = _entityId;
    final String? bank = _bank;
    if (file == null || entityId == null || bank == null) return;

    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.uploadStatement(
        entityId: entityId,
        sourceBank: bank,
        filename: file.name,
        bytes: file.bytes!,
      );
      if (!mounted) return;
      // Deliberately no success message. The import appears in the list
      // below with its own status, which says more than a toast would --
      // including when the file was refused.
      widget.onDone();
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = '$error';
      });
    }
  }

  bool get _ready => _file != null && _entityId != null && _bank != null;

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
          Text('Add a statement', style: text.titleMedium),
          const SizedBox(height: Spacing.lg),
          Wrap(
            spacing: Spacing.md,
            runSpacing: Spacing.md,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: <Widget>[
              _Field(
                label: 'Entity',
                child: DropdownButton<String>(
                  value: _entityId,
                  hint: const Text('Choose'),
                  underline: const SizedBox.shrink(),
                  items: <DropdownMenuItem<String>>[
                    for (final Entity e in _entities)
                      DropdownMenuItem<String>(
                        value: e.id,
                        child: Text(e.name),
                      ),
                  ],
                  onChanged: (String? v) => setState(() => _entityId = v),
                ),
              ),
              _Field(
                label: 'Bank',
                child: DropdownButton<String>(
                  value: _bank,
                  hint: const Text('Choose'),
                  underline: const SizedBox.shrink(),
                  items: <DropdownMenuItem<String>>[
                    for (final String b in _banks)
                      DropdownMenuItem<String>(value: b, child: Text(b)),
                  ],
                  onChanged: (String? v) => setState(() => _bank = v),
                ),
              ),
              _Field(
                label: 'File',
                child: TextButton(
                  onPressed: _pick,
                  child: Text(_file?.name ?? 'Choose a CSV'),
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
                onPressed: _ready && !_busy ? _upload : null,
                child: Text(_busy ? 'Uploading' : 'Upload'),
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
