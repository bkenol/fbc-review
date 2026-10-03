/**
 * What kind of set was chosen, from its name.
 *
 * Extracted from the upload page for the same reason `../viewer/fit` was: it is
 * the part that decides which request goes out, and testing it through a
 * component means standing up a form and a file input to check a regex.
 *
 * ## Why the name and not the bytes
 *
 * This is the immediate answer, not the safeguard. The server decides the kind
 * from the file's first bytes (`webapp/upload.py`, `sniff_bytes`) and is the only
 * place that counts: a DWG renamed `set.pdf` is stored and reviewed as a DWG, and
 * text renamed `.dwg` is refused. The name is what the browser has before a
 * byte is uploaded, and what decides two things that cannot wait for the
 * server's answer:
 *
 * - **Whether to read the set for the questionnaire.** Prefill runs inside the
 *   API request. For a drawing it would mean a DWG conversion and a 60 s read of
 *   the DXF in the web process — about 1.1 GB on the real 23 MB drawing — so the
 *   server refuses it (`prefill_not_available`) and the client does not ask.
 * - **Which options apply.** Rebuilding scanned sheets has nothing to rebuild in
 *   a plot made from the drawing, and the server ignores it for one.
 *
 * ## Why extensions in `accept`, not MIME types
 *
 * There is no settled MIME type for a DWG — `image/vnd.dwg`, `application/acad`
 * and an empty string are all seen — and a zip arrives as `application/zip` or
 * `application/x-zip-compressed` depending on the OS. An `accept` built on those
 * would hide the very files it means to offer in the native picker.
 */

/** A set this service reviews, by what it was uploaded as. */
export type UploadKind = 'pdf' | 'dwg' | 'dxf' | 'zip';

/** Every kind, in the order the copy names them. */
export const UPLOAD_KINDS: readonly UploadKind[] = ['pdf', 'dwg', 'dxf', 'zip'];

/** A name this service can review: a PDF, or the drawing itself. */
export const SET_FILE = /\.(pdf|dwg|dxf|zip)$/i;

/** A drawing rather than a plot of one. A zip is taken to hold drawings. */
export const CAD_FILE = /\.(dwg|dxf|zip)$/i;

/**
 * The file input's `accept`, when the deployment has not said otherwise.
 *
 * `application/pdf` is kept beside `.pdf` because that is what it always said,
 * and some pickers filter on the type before the extension.
 */
export const ACCEPT = '.pdf,application/pdf,.dwg,.dxf,.zip';

/** The kind a file name says it is, or null for anything this cannot review. */
export function uploadKind(name: string): UploadKind | null {
  const match = SET_FILE.exec(name);
  return match ? (match[1].toLowerCase() as UploadKind) : null;
}

/** Whether a file name is a drawing (DWG, DXF or a zip of them). */
export function isCad(name: string): boolean {
  return CAD_FILE.test(name);
}

/** The name with its set extension taken off, for the project-name field. */
export function projectName(name: string): string {
  return name.replace(SET_FILE, '');
}

/**
 * The file input's `accept` for the kinds this deployment admits.
 *
 * `/api/config` publishes `accepted_formats`: a deployment without a DWG
 * converter admits DXF and zip but not DWG, and offering a DWG in the picker
 * there is offering an upload that will be refused. An older server that does
 * not publish the list, or a config not yet loaded, gets every kind — the
 * server still checks.
 */
export function acceptFor(formats: readonly string[] | null | undefined): string {
  if (!formats?.length) return ACCEPT;
  const parts: string[] = [];
  for (const kind of UPLOAD_KINDS) {
    if (!formats.includes(kind)) continue;
    parts.push(`.${kind}`);
    if (kind === 'pdf') parts.push('application/pdf');
  }
  return parts.length ? parts.join(',') : ACCEPT;
}
