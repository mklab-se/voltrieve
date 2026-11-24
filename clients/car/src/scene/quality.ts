/** How good the picture can be on this computer. 0 is the best. Pure: it only gets the length of each frame. */

export const LEVELS = 4;

/** A steady 30 frames a second is a choice of the screen or of the battery, not a slow computer. */
const SLOW_S = 1 / 26;
/** Longer than this, and the tab was hidden. */
const HIDDEN_S = 0.5;
const WINDOW_FRAMES = 90;
/** Of the frames in a window: this many slow ones make the picture smaller, fewer than that make it good. */
const MOSTLY_SLOW = 0.6;
const GOOD = 0.1;
const FIRST_WAIT_WINDOWS = 6;

export class Quality {
  level = 0;
  private frames = 0;
  private slow = 0;
  private good = 0;
  /** Good windows before it tries the next better level. It doubles each time that this was too much. */
  private wait = FIRST_WAIT_WINDOWS;
  private raised = false;

  frame(seconds: number): void {
    if (seconds >= HIDDEN_S) return;
    this.frames += 1;
    if (seconds > SLOW_S) this.slow += 1;
    if (this.frames < WINDOW_FRAMES) return;
    const share = this.slow / this.frames;
    this.frames = this.slow = 0;
    if (share > MOSTLY_SLOW) this.fall();
    else if (share < GOOD) this.rise();
    else this.good = 0;
  }

  private fall(): void {
    if (this.raised) this.wait *= 2;
    this.raised = false;
    this.good = 0;
    this.level = Math.min(this.level + 1, LEVELS - 1);
  }

  private rise(): void {
    this.good += 1;
    if (this.level === 0 || this.good < this.wait) return;
    this.level -= 1;
    this.good = 0;
    this.raised = true;
  }
}
