/**
 * The review workspace: the set, its findings, and what you make of them.
 *
 * A separate route from the upload page rather than a panel on it. The upload
 * page is a form that ends in a result; this is somewhere you sit and work
 * through a set sheet by sheet, and it wants the whole window — which is what
 * `data: { chrome: 'full' }` in `app.routes.ts` gives it. It is also reachable
 * by URL, which matters: `/review/{id}` is what you send someone when you want
 * them to look at sheet 12 with you.
 *
 * Lazily routed, which is also what keeps pdf.js out of the initial bundle.
 *
 * ## The three things you can hand back
 *
 * 1. **Feedback on a finding** — the taxonomy, per finding.
 * 2. **Feedback on an abstention** — a rule that declined to run, which the
 *    register lists but which nothing could previously be said about. This is
 *    the path by which "the value is printed right there" becomes work.
 * 3. **A marked-up pass** — every annotation, exported for the reviewer and
 *    submitted as one piece for the owner.
 */
import { DatePipe, LowerCasePipe } from '@angular/common';
import {
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { map } from 'rxjs';

import {
  Abstention,
  AbstentionKindInfo,
  DeclarationField,
  Feedback,
  FeedbackSubject,
  Finding,
  Markup,
  MarkupRequest,
  ProjectDeclaration,
} from '../api';
import { FeedbackDraft, FeedbackPanel } from '../feedback/feedback-panel';
import { FocusRequest, SheetAnnotation, SheetViewer } from '../viewer/sheet-viewer';
import { TrainingService } from '../training/training-service';
import { AnswerNow } from './declaration/answer-now';
import { ReviewService } from './review-service';

@Component({
  selector: 'app-workspace',
  imports: [SheetViewer, FeedbackPanel, AnswerNow, RouterLink, DatePipe, LowerCasePipe],
  templateUrl: './workspace.html',
})
export class Workspace {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly reviews = inject(ReviewService);
  protected readonly training = inject(TrainingService);

  protected readonly job = this.reviews.job;
  protected readonly rerunning = this.reviews.rerunning;
  protected readonly findings = this.reviews.findings;
  protected readonly config = this.reviews.config;
  protected readonly failure = this.reviews.failure;

  protected readonly selectedFinding = signal<Finding | null>(null);
  protected readonly selectedMarkup = signal<Markup | null>(null);
  protected readonly selectedAbstention = signal<Abstention | null>(null);
  /** A comment that was already inside the uploaded file. */
  protected readonly selectedAnnot = signal<SheetAnnotation | null>(null);
  /** Every such comment the viewer found, across the whole set. */
  protected readonly annotations = signal<SheetAnnotation[]>([]);
  /** Which kind of feedback the panel is collecting right now. */
  protected readonly subject = signal<FeedbackSubject>(FeedbackSubject.Finding);
  protected readonly commentDraft = signal('');
  /** Which side list is open. The sheet keeps the space when they are closed. */
  protected readonly tab = signal<'findings' | 'notchecked' | 'comments' | 'markup'>(
    'findings',
  );

  /**
   * What the viewer should be looking at.
   *
   * A row in a register that does not take you to the sheet it is about is a
   * row you have to go and find, on a set where finding it means knowing which
   * of thirty-five sheets it is on. So picking one sends the viewer a focus
   * request, and the viewer pages to it, scrolls it into the middle and lights
   * it for a couple of seconds.
   *
   * The nonce is what makes picking the same row twice work. Without it the
   * input is unchanged on the second click and nothing happens — which is
   * exactly when you want it to, because you have scrolled away from the mark
   * and are asking to be taken back.
   */
  protected readonly focus = signal<FocusRequest | null>(null);
  private nonce = 0;
  /** The scrolling side column, so a card that opens can be scrolled to. */
  private readonly side = viewChild<ElementRef<HTMLElement>>('side');
  /** The hand-over form, opened from the markup tab. */
  protected readonly handingOver = signal(false);
  /**
   * Whether to offer the form on an abstention the classification calls
   * correct.
   *
   * The default is not to: "the set does not state this" is usually true, and
   * putting a complaint form under every one of a dozen such rows trains people
   * to fill it in. But the classification reads a reason string and has not
   * seen the drawing — when somebody says the value *is* printed on a sheet,
   * they are the one who looked, and the form has to open.
   */
  protected readonly forceProposal = signal(false);
  /** The sweep subject, for the hand-over panel. A template cannot name an
   *  enum member, and a bare string is not the enum. */
  protected readonly sweepSubject = FeedbackSubject.Sweep;

  /**
   * The review being looked at.
   *
   * Read from the `paramMap` stream rather than `route.snapshot`. The router
   * reuses this component when only the id changes — following a link from one
   * review to another — and a snapshot is captured once, so the second review
   * would never load.
   */
  protected readonly jobId = toSignal(
    this.route.paramMap.pipe(map((params) => params.get('id') ?? '')),
    { initialValue: '' },
  );

  protected readonly trainingOn = computed(
    () => this.config()?.training?.enabled ?? false,
  );
  protected readonly source = computed(() => this.job()?.downloads?.source_pdf ?? '');
  protected readonly reviewed = computed(() => this.job()?.downloads?.markup_pdf ?? '');
  protected readonly sheetIndex = computed(() => this.job()?.summary?.sheet_index ?? []);
  protected readonly aspects = computed(() => this.config()?.feedback_aspects ?? []);
  protected readonly markupKinds = computed(() => this.config()?.markup_kinds ?? []);
  protected readonly markupColours = computed(() => this.config()?.markup_colours ?? []);

  protected readonly open = computed(() =>
    (this.findings() ?? []).filter((f) => f.status !== 'PASS'),
  );

  // ── what did not get checked ────────────────────────────────────────────
  protected readonly abstentions = computed(
    () => this.job()?.summary?.abstentions ?? [],
  );
  protected readonly diagnosis = computed(() => this.job()?.diagnosis ?? []);

  /** Abstentions worth offering a proposal for, most useful first. */
  protected readonly proposable = computed(() =>
    this.abstentions().filter((a) => a.proposable),
  );

  /**
   * How many stood down waiting on a question somebody can answer here.
   *
   * Counted rather than listed at the top of the register: the remedy is the
   * same sentence for all of them, and saying it once above the list is the
   * difference between a hint and twelve copies of a hint.
   */
  protected readonly answerableCount = computed(
    () => this.abstentions().filter((a) => (a.unlocked_by ?? []).length).length,
  );

  private readonly kindIndex = computed(() => {
    const index = new Map<string, AbstentionKindInfo>();
    for (const kind of this.config()?.abstention_kinds ?? []) index.set(kind.key, kind);
    return index;
  });

  protected kindOf(abstention: Abstention): AbstentionKindInfo | null {
    return this.kindIndex().get(abstention.kind ?? '') ?? null;
  }

  /** Feedback this reviewer has already given, keyed by what it was about. */
  protected readonly saidAbout = computed(() => {
    const map = new Map<string, Feedback>();
    for (const item of this.training.feedback()) {
      if (item.finding_fid) map.set(item.finding_fid, item);
    }
    return map;
  });

  /** The same, for rules rather than findings. */
  protected readonly saidAboutRule = computed(() => {
    const map = new Map<string, Feedback>();
    for (const item of this.training.feedback()) {
      if (item.subject === FeedbackSubject.Abstention && item.rule_id) {
        map.set(item.rule_id, item);
      }
    }
    return map;
  });

  constructor() {
    this.reviews.loadConfig();

    effect(() => {
      const id = this.jobId();
      if (!id) return;
      // Opening a different review must not leave the previous one's selection
      // and markup on screen.
      this.clearSelection();
      this.handingOver.set(false);
      this.annotations.set([]);
      this.focus.set(null);
      this.training.clearExport();
      this.reviews.open(id);
    });

    // Markup and feedback only exist on a deployment that collects them, and
    // only once the config has said so — asking first would 400 on every
    // deployment that has training switched off.
    effect(() => {
      const id = this.jobId();
      if (id && this.trainingOn()) {
        this.training.loadMarkups(id);
        this.training.loadFeedback(id);
      }
    });
  }

  // ── selection ───────────────────────────────────────────────────────────
  /**
   * Picked on the drawing itself.
   *
   * No focus request: the sheet is already in front of you and scrolling it
   * out from under the cursor you just clicked with is the opposite of helpful.
   * Picking the same thing from a register does send one — see `openFinding`.
   */
  protected pickFinding(finding: Finding): void {
    this.selectedFinding.set(finding);
    this.selectedMarkup.set(null);
    this.selectedAbstention.set(null);
    this.selectedAnnot.set(null);
    this.subject.set(FeedbackSubject.Finding);
    this.training.clearAccepted();
  }

  /** Picked from the register: select it, and take the viewer to it. */
  protected openFinding(finding: Finding): void {
    this.pickFinding(finding);
    this.showPanelTop();
    this.lookAt('finding', finding.fid, finding.page);
  }

  protected pickMarkup(markup: Markup): void {
    this.selectedMarkup.set(markup);
    this.selectedFinding.set(null);
    this.selectedAbstention.set(null);
    this.selectedAnnot.set(null);
    this.commentDraft.set(markup.comment ?? '');
    this.training.clearAccepted();
  }

  protected openMarkup(markup: Markup): void {
    this.pickMarkup(markup);
    this.showPanelTop();
    this.lookAt('markup', markup.id, markup.page);
  }

  /** A comment that came with the file. Nothing here is feedback on the review. */
  protected pickAnnotation(mark: SheetAnnotation): void {
    this.selectedAnnot.set(mark);
    this.selectedFinding.set(null);
    this.selectedMarkup.set(null);
    this.selectedAbstention.set(null);
    this.training.clearAccepted();
  }

  protected openAnnotation(mark: SheetAnnotation): void {
    this.pickAnnotation(mark);
    this.showPanelTop();
    this.lookAt('annotation', mark.id, mark.page);
  }

  protected onAnnotationsRead(marks: SheetAnnotation[]): void {
    this.annotations.set(marks);
  }

  private lookAt(kind: FocusRequest['kind'], id: string, page: number): void {
    this.focus.set({ kind, id, page, nonce: ++this.nonce });
  }

  /**
   * Put the card that just opened where somebody can see it.
   *
   * The panel is one scrolling column: the detail card on top, the register
   * underneath. Picking a row inserts the card *above* the row, and the browser
   * keeps the button you clicked where it is — so the card you asked for opens
   * off the top of the panel and the panel looks like it did nothing. It is
   * worst on the abstention register, where the card carries the question that
   * would let the rule run.
   */
  private showPanelTop(): void {
    this.side()?.nativeElement.scrollTo({ top: 0, behavior: 'smooth' });
  }

  /**
   * Open a rule that stood down.
   *
   * The panel opens on the verdict the classification implies, which the person
   * may change before submitting. Pre-selecting is not deciding: nothing is
   * sent that they did not confirm, and the one class where the abstention was
   * probably correct deliberately pre-selects nothing.
   */
  protected pickAbstention(abstention: Abstention): void {
    this.selectedAbstention.set(abstention);
    this.selectedFinding.set(null);
    this.selectedMarkup.set(null);
    this.selectedAnnot.set(null);
    this.subject.set(FeedbackSubject.Abstention);
    this.forceProposal.set(false);
    this.training.clearAccepted();
    // An abstention has no mark on the drawing to page to, so this is the only
    // thing that happens when you pick one — and it is the case where the card
    // matters most, because it carries the question that unlocks the rule.
    this.showPanelTop();
  }

  /** Open the form on an abstention this build thinks was correct. */
  protected reportAnyway(): void {
    this.forceProposal.set(true);
  }

  // ── answering what the rule was waiting on ──────────────────────────────
  /**
   * The declaration questions the selected abstention named.
   *
   * `unlocked_by` is inverted on the server from the schema's own `unlocks`, so
   * this is the rule's own account of what it wanted rather than a guess made
   * here. The metadata — label, kind, choices, help — comes from `/api/config`,
   * which is the same source the full questionnaire renders from, so a retitled
   * question is retitled in both places at once.
   */
  protected readonly answerable = computed<DeclarationField[]>(() => {
    const keys = new Set(this.selectedAbstention()?.unlocked_by ?? []);
    if (!keys.size) return [];
    return (this.config()?.declaration_fields ?? []).filter((f) => keys.has(f.key));
  });

  /**
   * Start a second review with the answer folded in.
   *
   * Navigating to the new id rather than staying put: the review in front of
   * you is not the one that now has the answer, and leaving it on screen while
   * a different review runs is how somebody ends up reading a stale register
   * and believing it.
   */
  protected answerAndRerun(declaration: ProjectDeclaration): void {
    const id = this.jobId();
    if (!id) return;
    this.reviews.rerun(id, declaration, (next) => {
      void this.router.navigate(['/review', next]);
    });
  }

  /** Whether to show the proposal form for the abstention in hand. */
  protected readonly canPropose = computed(
    () => (this.selectedAbstention()?.proposable ?? false) || this.forceProposal(),
  );

  protected clearSelection(): void {
    this.selectedFinding.set(null);
    this.selectedMarkup.set(null);
    this.selectedAbstention.set(null);
    this.selectedAnnot.set(null);
    this.subject.set(FeedbackSubject.Finding);
    this.forceProposal.set(false);
    this.training.clearAccepted();
  }

  /** Switch the form to "this sheet is missing a check", anchored to a markup. */
  protected reportGap(markup: Markup): void {
    this.selectedMarkup.set(markup);
    this.selectedFinding.set(null);
    this.selectedAbstention.set(null);
    this.selectedAnnot.set(null);
    this.subject.set(FeedbackSubject.Coverage);
    this.training.clearAccepted();
  }

  /**
   * The verdict to open the abstention form on.
   *
   * Mirrors `webapp.abstentions.prefill`, and is the one piece of the taxonomy
   * this client decides for itself. It is a starting position for a form rather
   * than an answer: the submitted value is whatever the person leaves selected.
   *
   * A `computed` rather than a method called from the template, and that is
   * load-bearing rather than tidiness. A method returning an object literal
   * hands the input a new identity on every change detection, the panel's
   * re-seed effect fires each time, and the reviewer's answers are wiped as
   * they work. Keyed on the selection, the identity only changes when the
   * thing being asked about does.
   */
  protected readonly prefill = computed((): Record<string, string> => {
    const abstention = this.selectedAbstention();
    if (!abstention) return {};
    const suggestion: Record<string, string> = {
      extraction: 'data_on_sheet',
      geometry: 'layer_named_differently',
      corpus: 'corpus_missing',
      error: 'rule_failed',
    };
    const verdict = suggestion[abstention.kind ?? ''];
    return verdict ? { standdown: verdict } : {};
  });

  // ── markup ──────────────────────────────────────────────────────────────
  protected onDrawn(request: MarkupRequest): void {
    this.training.createMarkup(this.jobId(), request, (created) => {
      // A shape you have just drawn is the thing you want to write about, and
      // a text label is unreadable until you have. Selecting it puts the
      // caret one click away instead of making you find it again.
      this.pickMarkup(created);
      this.tab.set('markup');
    });
  }

  protected onCommentInput(event: Event): void {
    this.commentDraft.set((event.target as HTMLTextAreaElement).value);
  }

  protected saveComment(): void {
    const markup = this.selectedMarkup();
    if (!markup) return;
    this.training.updateMarkup(this.jobId(), markup, {
      page: markup.page,
      kind: markup.kind,
      geometry: markup.geometry,
      comment: this.commentDraft(),
      colour: markup.colour ?? '',
      sheet: markup.sheet ?? '',
      finding_fid: markup.finding_fid ?? '',
    });
  }

  protected recolour(key: string): void {
    const markup = this.selectedMarkup();
    if (!markup) return;
    this.training.updateMarkup(this.jobId(), markup, {
      page: markup.page,
      kind: markup.kind,
      geometry: markup.geometry,
      comment: this.commentDraft(),
      colour: markup.colour === key ? '' : key,
      sheet: markup.sheet ?? '',
      finding_fid: markup.finding_fid ?? '',
    });
  }

  protected removeMarkup(markup: Markup): void {
    this.training.deleteMarkup(this.jobId(), markup.id);
    if (this.selectedMarkup()?.id === markup.id) this.selectedMarkup.set(null);
  }

  // ── handing the pass over ───────────────────────────────────────────────
  /** Fetch the bundle so it can be read before it is sent, or just kept. */
  protected preview(): void {
    this.handingOver.set(true);
    this.training.loadExport(this.jobId());
  }

  /**
   * Save the pass to a file.
   *
   * A blob rather than a link to an endpoint: a download is a navigation and
   * carries no `Authorization` header, so an `<a href>` at the export route
   * would 401 on any deployment that has sign-in switched on.
   */
  protected download(): void {
    const bundle = this.training.markupExport();
    if (!bundle) return;
    const name = (bundle.project_name || bundle.filename || bundle.job_id).replace(
      /[^\w.-]+/g,
      '-',
    );
    const blob = new Blob([JSON.stringify(bundle, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${name}-markup.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  protected handOver(draft: FeedbackDraft): void {
    this.training.submitPass(this.jobId(), {
      answers: draft.answers,
      comment: draft.comment,
    });
  }

  protected cancelHandover(): void {
    this.handingOver.set(false);
    this.training.clearExport();
    this.training.clearAccepted();
  }

  // ── feedback ────────────────────────────────────────────────────────────
  protected onFeedback(draft: FeedbackDraft): void {
    this.training.submitFeedback(this.jobId(), {
      subject: this.subject(),
      finding_fid: this.selectedFinding()?.fid ?? '',
      markup_id: this.selectedMarkup()?.id ?? '',
      rule_id: this.selectedAbstention()?.rule ?? '',
      answers: draft.answers,
      comment: draft.comment,
    });
  }

  protected feedbackHeadline(): string {
    if (this.subject() === FeedbackSubject.Coverage) {
      return 'What should this review have caught here?';
    }
    if (this.subject() === FeedbackSubject.Abstention) {
      const abstention = this.selectedAbstention();
      return abstention ? `${abstention.rule} declined to run` : '';
    }
    const finding = this.selectedFinding();
    return finding ? `${finding.fid} · ${finding.title}` : '';
  }
}
