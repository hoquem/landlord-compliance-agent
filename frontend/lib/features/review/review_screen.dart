/// The review queue: the screen this product exists for.
///
/// **Proposal leads, evidence beneath.** Line one is the agent's answer;
/// line two is the raw bank narrative, always present and never hidden in a
/// tooltip. That ordering was a deliberate choice (2026-08-04) and it has a
/// consequence: if the machine's answer is what you read first, the
/// *uncertain* ones are the dangerous ones.
///
/// So **confidence drives order and weight, never colour**. Anything below
/// [kLowConfidence] sorts to the top and renders its proposal at regular
/// weight rather than semibold, so it looks less settled — which is exactly
/// what it is. The plan asked for an amber tint and a pulse; `DESIGN.md`
/// rules both out, because a low-confidence proposal is not an error and
/// tinting it would collide with the status vocabulary.
///
/// **The screen calms as you work.** Confirmed rows recede to a neutral,
/// because settled work should stop asking for attention. That is the core
/// loop's reward, and it lives in the palette rather than in a celebration.
library;

import 'package:flutter/material.dart';

import '../../api/api_client.dart';
import '../../api/models.dart';
import '../../api/money.dart';
import '../../app/widgets/screen_scaffold.dart';
import '../../app/widgets/status_pill.dart';
import '../../theme/status_colors.dart';
import '../../theme/tokens.dart';
import 'category_picker.dart';

class ReviewScreen extends StatefulWidget {
  const ReviewScreen({required this.api, super.key});

  final ApiClient api;

  @override
  State<ReviewScreen> createState() => _ReviewScreenState();
}

class _ReviewScreenState extends State<ReviewScreen> {
  List<Txn>? _txns;
  List<PropertyRef> _properties = <PropertyRef>[];
  List<String> _categories = <String>[];
  final Set<String> _selected = <String>{};

  /// Interest inputs, one per repayment-mortgage row.
  ///
  /// Held here and not built in `build`: a `TextEditingController` created
  /// during a rebuild resets the cursor on every keystroke, which the
  /// ownership editor already paid for once.
  final Map<String, TextEditingController> _interest =
      <String, TextEditingController>{};
  final Map<String, String> _overrides = <String, String>{};
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final List<Txn> txns = await widget.api.listTransactions();
      final List<PropertyRef> properties = await widget.api.listProperties();
      final List<String> categories = await widget.api.listCategories();
      if (!mounted) return;
      setState(() {
        _txns = _ordered(txns);
        _properties = properties;
        _categories = categories;
        _error = null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '$error');
    }
  }

  /// Least certain first, then by date.
  ///
  /// A line with no proposal at all is the least certain of the lot, so it
  /// sorts above a low-confidence one rather than below it.
  static List<Txn> _ordered(List<Txn> txns) {
    final List<Txn> sorted = <Txn>[...txns];
    sorted.sort((Txn a, Txn b) {
      if (a.needsDecision != b.needsDecision) return a.needsDecision ? -1 : 1;
      final double ca = a.confidence ?? -1;
      final double cb = b.confidence ?? -1;
      if (ca != cb) return ca.compareTo(cb);
      return a.date.compareTo(b.date);
    });
    return sorted;
  }

  @override
  void dispose() {
    for (final TextEditingController c in _interest.values) {
      c.dispose();
    }
    super.dispose();
  }

  String? _categoryFor(Txn txn) => _overrides[txn.id] ?? txn.hmrcCategory;

  /// Whether this line's payment mixes allowable interest with capital.
  ///
  /// True only for a finance cost on a property whose borrowing is a
  /// repayment mortgage. The backend refuses to export such a line without
  /// an interest figure, so collecting it here is what stops that refusal
  /// being a dead end.
  bool _needsInterest(Txn txn) {
    if (!kFinanceCostCategories.contains(_categoryFor(txn))) return false;
    final PropertyRef? property = _propertyFor(txn);
    return property != null && property.needsInterestSplit;
  }

  TextEditingController _interestController(Txn txn) =>
      _interest.putIfAbsent(txn.id, TextEditingController.new);

  /// The interest figure for a row, or `null` if it is missing or unusable.
  ///
  /// Returns `null` rather than throwing on nonsense: the row simply stays
  /// unconfirmable, and [_interestProblem] is what explains why.
  String? _interestFor(Txn txn) {
    final String raw = (_interest[txn.id]?.text ?? '').trim();
    if (raw.isEmpty) return null;
    final double? value = double.tryParse(raw);
    if (value == null || value <= 0 || value > txn.amount) return null;
    return raw;
  }

  /// What to tell the user about an interest figure they have started typing.
  String? _interestProblem(Txn txn) {
    final String raw = (_interest[txn.id]?.text ?? '').trim();
    if (raw.isEmpty) return null;
    final double? value = double.tryParse(raw);
    if (value == null) return 'That is not a number.';
    if (value <= 0) return 'The interest has to be more than nothing.';
    if (value > txn.amount) return 'That is more than the payment.';
    return null;
  }

  /// Selected lines that actually have a category to confirm.
  List<ConfirmItem> get _confirmable => <ConfirmItem>[
    for (final Txn txn in _txns ?? <Txn>[])
      if (_selected.contains(txn.id) &&
          _categoryFor(txn) != null &&
          (!_needsInterest(txn) || _interestFor(txn) != null))
        ConfirmItem(
          transactionId: txn.id,
          hmrcCategory: _categoryFor(txn)!,
          propertyId: txn.propertyId,
          allowableAmount: _needsInterest(txn) ? _interestFor(txn) : null,
        ),
  ];

  /// Selected, confirmable lines the agent itself was unsure about.
  ///
  /// Surfaced on the button because this is where the product's promise is
  /// thinnest. `PRODUCT.md`: *"Refusing is a feature, so make refusal feel
  /// like protection."* Refusal is enforced at **export** -- an unreviewed
  /// line stops the return outright -- but at **review** nothing costs you
  /// anything for accepting a proposal without reading it. This does not add
  /// a barrier; it makes the choice visible, so skipping is something you
  /// did rather than something you did not notice.
  ///
  /// Pinned by `the button says how many uncertain proposals it accepts`.
  int get _uncertainSelected => <Txn>[
    for (final Txn txn in _txns ?? <Txn>[])
      if (_selected.contains(txn.id) &&
          _categoryFor(txn) != null &&
          txn.confidence != null &&
          txn.confidence! < kLowConfidence)
        txn,
  ].length;

  Future<void> _confirmSelected() async {
    final List<ConfirmItem> items = _confirmable;
    if (items.isEmpty) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      // One call, not one per row: the backend applies the batch in a single
      // transaction and abandons all of it on any failure, so a partly
      // applied screenful cannot happen.
      await widget.api.confirmBatch(items);
      if (!mounted) return;
      _selected.clear();
      _overrides.clear();
      setState(() => _busy = false);
      await _load();
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
    final List<Txn> txns = _txns ?? <Txn>[];
    final int outstanding = txns.where((Txn t) => t.needsDecision).length;

    return ScreenScaffold(
      title: 'Review',
      subtitle: outstanding == 0
          ? 'Nothing is waiting on a decision.'
          : '$outstanding ${outstanding == 1 ? 'line needs' : 'lines need'} a decision.',
      // Keyed on what is *confirmable*, not on what is selected. A line with
      // no category yet is selectable but gets dropped on the way out, so
      // counting the selection made the button promise work it would not do.
      // Pinned by `the button counts what it will actually confirm`.
      action: _confirmable.isEmpty
          ? null
          : FilledButton(
              onPressed: _busy ? null : _confirmSelected,
              child: Text(
                _busy
                    ? 'Confirming'
                    : 'Confirm ${_confirmable.length}'
                          '${_uncertainSelected > 0 ? ' · $_uncertainSelected uncertain' : ''}',
              ),
            ),
      child: ListView(
        padding: const EdgeInsets.only(bottom: Spacing.xl),
        children: <Widget>[
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
          if (_txns != null && txns.isEmpty) const _NothingToReview(),
          if (_txns != null && txns.isNotEmpty && outstanding == 0)
            const _QueueCleared(),
          for (final Txn txn in txns)
            _ReviewRow(
              txn: txn,
              category: _categoryFor(txn),
              property: _propertyFor(txn),
              selected: _selected.contains(txn.id),
              interestController: _needsInterest(txn)
                  ? _interestController(txn)
                  : null,
              interestProblem: _interestProblem(txn),
              interestSettled: !_needsInterest(txn) || _interestFor(txn) != null,
              onInterestChanged: () => setState(() {}),
              onToggle: () => setState(() {
                if (!_selected.remove(txn.id)) _selected.add(txn.id);
              }),
              onPickCategory: (BuildContext anchor) async {
                final String? chosen = await pickCategory(
                  anchor,
                  categories: _categories,
                  current: _categoryFor(txn),
                );
                if (chosen == null || !mounted) return;
                setState(() {
                  _overrides[txn.id] = chosen;
                  _selected.add(txn.id);
                });
              },
            ),
        ],
      ),
    );
  }

  PropertyRef? _propertyFor(Txn txn) {
    for (final PropertyRef p in _properties) {
      if (p.id == txn.propertyId) return p;
    }
    return null;
  }
}

class _ReviewRow extends StatelessWidget {
  const _ReviewRow({
    required this.txn,
    required this.category,
    required this.property,
    required this.selected,
    required this.onToggle,
    required this.interestController,
    required this.interestProblem,
    required this.interestSettled,
    required this.onInterestChanged,
    required this.onPickCategory,
  });

  final Txn txn;
  final String? category;
  final PropertyRef? property;
  final bool selected;
  final VoidCallback onToggle;

  /// Non-null only when this payment mixes interest with capital.
  final TextEditingController? interestController;
  final String? interestProblem;
  final bool interestSettled;
  final VoidCallback onInterestChanged;
  final void Function(BuildContext anchor) onPickCategory;

  bool get _uncertain =>
      txn.confidence != null && txn.confidence! < kLowConfidence;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final StatusColors colors = Theme.of(context).extension<StatusColors>()!;
    final bool settled = !txn.needsDecision;

    // An instruction, not a restatement of the pill beside it. The pill
    // says the state ("Not categorised"); this line is what you press, so
    // it says what pressing it does.
    final String proposal = category == null
        ? 'Choose a category'
        : <String>[
            categoryLabel(category!),
            if (property != null) property!.label,
          ].join(' · ');

    return Column(
      children: <Widget>[
        Container(
          color: selected ? palette.bgSurface : null,
          padding: const EdgeInsets.symmetric(
            horizontal: Spacing.xl,
            vertical: Spacing.sm + Spacing.xs,
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Checkbox(
                value: selected,
                // Unconfirmable until the split is supplied. Refusing here,
                // where it is fixable, beats refusing at export where it is a
                // dead end -- PRODUCT.md: "make refusal feel like protection".
                onChanged: settled || !interestSettled ? null : (_) => onToggle(),
              ),
              const SizedBox(width: Spacing.sm),
              SizedBox(
                width: 68,
                child: Padding(
                  padding: const EdgeInsets.only(top: Spacing.xs),
                  child: Text(_shortDate(txn.date), style: AppType.meta.copyWith(color: palette.textMuted)),
                ),
              ),
              Expanded(
                child: Builder(
                  builder: (BuildContext anchor) => InkWell(
                    onTap: settled ? null : () => onPickCategory(anchor),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Row(
                          children: <Widget>[
                            Flexible(
                              child: Text(
                                proposal,
                                style: AppType.title.copyWith(
                                  // Settled work recedes; an uncertain
                                  // proposal is deliberately lighter, so it
                                  // *looks* less settled than a confident one.
                                  color: settled
                                      ? palette.textMuted
                                      : palette.textHigh,
                                  fontWeight: _uncertain
                                      ? FontWeight.w400
                                      : FontWeight.w600,
                                ),
                              ),
                            ),
                            // Gated on `!settled`, not on confidence alone.
                            // "check this" is an instruction, and a human who
                            // has confirmed the line has already followed it;
                            // confidence never changes afterwards, so the
                            // ungated version left settled rows nagging about
                            // work that was done. Pinned by
                            // `a confirmed line stops asking to be checked`.
                            if (_uncertain && !settled) ...<Widget>[
                              const SizedBox(width: Spacing.sm),
                              Text(
                                'check this',
                                style: AppType.meta.copyWith(
                                  color: colors.needsYou,
                                ),
                              ),
                            ],
                          ],
                        ),
                        const SizedBox(height: 2),
                        Text(
                          txn.description,
                          style: AppType.meta.copyWith(
                            color: palette.textMuted,
                          ),
                        ),
                        if (interestController != null)
                          _InterestSplit(
                            controller: interestController!,
                            problem: interestProblem,
                            onChanged: onInterestChanged,
                          ),
                      ],
                    ),
                  ),
                ),
              ),
              const SizedBox(width: Spacing.md),
              Padding(
                padding: const EdgeInsets.only(top: Spacing.xs / 2),
                child: Text(
                  formatMoney(txn.signedForDisplay),
                  // Never coloured by sign: an expense is not an error.
                  style: AppType.numeric.copyWith(
                    color: settled ? palette.textMuted : palette.textHigh,
                  ),
                ),
              ),
              const SizedBox(width: Spacing.md),
              SizedBox(
                width: 104,
                child: Align(
                  alignment: Alignment.centerRight,
                  child: StatusPill(state: txn.state, label: txn.label),
                ),
              ),
            ],
          ),
        ),
        Divider(height: 1, thickness: 1, color: palette.rule),
      ],
    );
  }

  static String _shortDate(DateTime d) {
    const List<String> months = <String>[
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    return '${d.day} ${months[d.month - 1]}';
  }
}

class _NothingToReview extends StatelessWidget {
  const _NothingToReview();

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.all(Spacing.xl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('Nothing to review.', style: text.titleMedium),
          const SizedBox(height: Spacing.xs),
          Text(
            'Upload a statement and the categoriser will propose against it.',
            style: text.bodyMedium?.copyWith(color: palette.textMuted),
          ),
        ],
      ),
    );
  }
}

/// The one earned moment on this screen.
///
/// Not confetti: the subject is a tax return. The list has already drained
/// of colour by the time this appears, which is most of the reward.
class _QueueCleared extends StatelessWidget {
  const _QueueCleared();

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        Spacing.xl,
        Spacing.xl,
        Spacing.xl,
        Spacing.lg,
      ),
      child: Text(
        "That's everything reviewed.",
        style: Theme.of(
          context,
        ).textTheme.displaySmall?.copyWith(color: palette.textBody),
      ),
    );
  }
}


/// The interest figure for a repayment-mortgage payment.
///
/// **Why this exists at all.** A repayment mortgage leaves the account as one
/// direct debit that is part interest and part capital. Only the interest is
/// an allowable expense; the capital repays a loan. The bank line carries one
/// number and nothing in the system can derive the split — a stored ratio
/// would be wrong every month as the balance amortises — so it has to come
/// from the lender's statement, which means from a person.
///
/// Inline rather than a popover, unlike the category picker: this is a figure
/// you copy off another document while looking at the row, not a choice from
/// a list, and hiding it behind a tap would make you lose your place.
class _InterestSplit extends StatelessWidget {
  const _InterestSplit({
    required this.controller,
    required this.problem,
    required this.onChanged,
  });

  final TextEditingController controller;
  final String? problem;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final StatusColors colors = Theme.of(context).extension<StatusColors>()!;

    return Padding(
      padding: const EdgeInsets.only(top: Spacing.sm),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: <Widget>[
          SizedBox(
            width: 118,
            child: TextField(
              controller: controller,
              onChanged: (_) => onChanged(),
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              style: AppType.numeric.copyWith(color: palette.textHigh),
              decoration: InputDecoration(
                isDense: true,
                hintText: 'Interest',
                hintStyle: AppType.meta.copyWith(color: palette.textMuted),
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: Spacing.sm,
                  vertical: Spacing.sm,
                ),
              ),
            ),
          ),
          const SizedBox(width: Spacing.sm),
          Flexible(
            child: Text(
              // States the fact, then what to do about it. Never scolds: the
              // agent was right about the category, and the user has done
              // nothing wrong by not knowing a figure only the lender holds.
              problem ??
                  'Repayment mortgage — only the interest is allowable. '
                      'Enter it from your lender statement.',
              style: AppType.meta.copyWith(
                color: problem == null ? palette.textMuted : colors.wrong,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
