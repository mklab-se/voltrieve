/** Runa, the Voltrieve assistant (issue #15). The avatar is an emblem, never a face: Runa is an AI. */
export const ASSISTANT_NAME = "Runa";

export function RunaAvatar({ size = "small" }: { size?: "small" | "large" }) {
  // Decorative: the name always appears next to it, so screen readers skip the image.
  return <img className={`avatar ${size}`} src={`${import.meta.env.BASE_URL}runa.png`} alt="" />;
}
