/* Shell side of bi-doc-viewer protocol v1 (handoff 16, 17.1).

   The viewer runs in a sandboxed iframe without allow-same-origin, so its origin is
   opaque ("null") and cannot be trusted by name. Messages are accepted only when
   event.source is the active iframe's window, the per-load channel and revision match,
   and the shape is exact. The shell sends only registered view IDs with string args,
   and only after the viewer has answered the handshake. */

export interface ViewTarget { view_id: string; args: Record<string, string | number | boolean>; }
export interface Navigated { object_id: string | null; exact: boolean; }

const PROTOCOL = "bi-doc-viewer";

function randomChannel(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export class ViewerChannel {
  readonly channel = randomChannel();
  private ready = false;
  private views = new Set<string>();
  private pending: { objectId: string | null; target: ViewTarget } | null = null;
  private readonly listener: (ev: MessageEvent) => void;

  constructor(private frame: HTMLIFrameElement, private revisionId: string,
              private onReady: (views: string[]) => void, private onNavigated: (n: Navigated) => void) {
    this.listener = (ev) => this.receive(ev);
    window.addEventListener("message", this.listener);
    frame.addEventListener("load", () => this.hello());
  }

  close(): void { window.removeEventListener("message", this.listener); }

  get isReady(): boolean { return this.ready; }

  private post(message: Record<string, unknown>): void {
    // The target origin is opaque, so "*" is the only possible value; messages hold no secrets.
    this.frame.contentWindow?.postMessage({ protocol: PROTOCOL, version: 1, channel: this.channel,
      revision_id: this.revisionId, ...message }, "*");
  }

  private hello(): void { this.post({ type: "hello" }); }

  navigate(objectId: string | null, target: ViewTarget): void {
    if (!this.ready) { this.pending = { objectId, target }; return; }
    if (!this.views.has(target.view_id)) { this.onNavigated({ object_id: objectId, exact: false }); return; }
    this.post({ type: "navigate-object", object_id: objectId, view_id: target.view_id, args: target.args });
  }

  private receive(ev: MessageEvent): void {
    if (ev.source !== this.frame.contentWindow) return;
    const m = ev.data as Record<string, unknown> | null;
    if (!m || typeof m !== "object" || m.protocol !== PROTOCOL || m.version !== 1) return;
    if (m.channel !== this.channel || m.revision_id !== this.revisionId) return;
    if (m.type === "viewer-ready" && !this.ready && Array.isArray(m.views)) {
      this.ready = true;
      this.views = new Set(m.views.filter((v): v is string => typeof v === "string" && v.length <= 64));
      this.onReady([...this.views]);
      if (this.pending) { const p = this.pending; this.pending = null; this.navigate(p.objectId, p.target); }
    } else if (m.type === "navigated" && this.ready) {
      this.onNavigated({ object_id: typeof m.object_id === "string" ? m.object_id : null, exact: m.exact === true });
    }
  }
}
