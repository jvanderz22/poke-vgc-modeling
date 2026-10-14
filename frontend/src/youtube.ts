// The YouTube IFrame API: the embedded player, and the time it is at. Every frame the watch page
// reads or records is stamped with that time, so what is read can be lined up with the video
// (docs/PLAN-video.md, "Every entry carries its video time").

/** The part of YT.Player this app uses. */
export type YouTubePlayer = {
  getCurrentTime(): number;
  getPlayerState(): number; // 1 is playing
  destroy(): void;
};

type YT = {
  Player: new (el: HTMLElement, opts: {
    videoId: string;
    playerVars?: Record<string, number | string>;
    events?: { onReady?: () => void; onError?: (e: { data: number }) => void };
  }) => YouTubePlayer;
};

let api: Promise<YT> | null = null;

function loadApi(): Promise<YT> {
  if (!api) {
    api = new Promise((ok) => {
      const w = window as unknown as { YT?: YT; onYouTubeIframeAPIReady?: () => void };
      if (w.YT?.Player) { ok(w.YT); return; }
      const before = w.onYouTubeIframeAPIReady;
      w.onYouTubeIframeAPIReady = () => { before?.(); ok(w.YT!); };
      const s = document.createElement("script");
      s.src = "https://www.youtube.com/iframe_api";
      document.head.appendChild(s);
    });
  }
  return api;
}

/** Why the player refused a video, in words (the API's error codes). */
export function playerError(code: number): string {
  if (code === 101 || code === 150) return "The video's owner doesn't allow it to be played outside YouTube.";
  if (code === 100) return "That video is private or has been removed.";
  return `The player couldn't play that video (error ${code}).`;
}

/** Put a player for `video` into `host`, starting at `start` seconds. It replaces whatever the host
 *  held, and the returned function takes it down again. */
export function mountPlayer(host: HTMLElement, video: string, start: number,
                            onReady: (p: YouTubePlayer) => void, onError: (message: string) => void): () => void {
  let live = true;
  let player: YouTubePlayer | null = null;
  host.replaceChildren();
  const el = document.createElement("div");
  host.appendChild(el);
  loadApi().then((YT) => {
    if (!live) return;
    player = new YT.Player(el, {
      videoId: video,
      playerVars: { start, rel: 0, playsinline: 1 },
      events: {
        onReady: () => { if (live && player) onReady(player); },
        onError: (e) => { if (live) onError(playerError(e.data)); },
      },
    });
  });
  return () => {
    live = false;
    player?.destroy();
    host.replaceChildren();
  };
}
