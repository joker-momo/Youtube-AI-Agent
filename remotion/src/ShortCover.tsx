/**
 * Vertical Short cover (1080x1920) — spec v6 §12 + universal template §11.
 *
 * Primary cover renderer. Reuses the first scene's background and renders the
 * hook text in the spec's hook zone (y=460-760). No CTA. Logo in safe top-right.
 */
import React from 'react';
import {AbsoluteFill} from 'remotion';
import {RenderProps} from './render-props';
import {ShortBackground} from './shorts/ShortBackground';
import {ShortHookLayout} from './shorts/ShortLayouts';

function pickOverlay(visual: string, narration: string): 'default' | 'dark' | 'bright' {
  const text = `${visual} ${narration}`.toLowerCase();
  if (/(night|sleep|dark|noche|sueño|cama)/.test(text)) return 'dark';
  if (/(bright|sun|daylight|sol|cocina)/.test(text)) return 'bright';
  return 'default';
}

export const ShortCover: React.FC<RenderProps> = (props) => {
  const first = (props.scenes && props.scenes[0]) || ({} as any);
  const layoutPayload = first.layout_payload || {};
  const cover_text = (layoutPayload as any).cover_text;
  const title =
    cover_text ||
    layoutPayload.title ||
    first.on_screen_text ||
    (props as any)?.seo?.title ||
    '';

  const bg = (first as any).asset_refs?.background as string | undefined;
  const overlay = pickOverlay(first.visual_prompt || '', first.narration || '');

  return (
    <AbsoluteFill style={{backgroundColor: '#0b1020'}}>
      {/* Brighten + tweak contrast/saturation of the cover background only
         (does not affect the rendered video). Tuned after legibility review. */}
      <AbsoluteFill
        style={{
          filter: 'brightness(1.12) contrast(1.10) saturate(1.05)',
        }}
      >
        <ShortBackground src={bg} overlay={overlay} />
      </AbsoluteFill>
      {/* Cover-only title stroke boost — thicker outline pulls the title
         off any leftover bright pixels in the photo so it pops on the YT
         dark UI. ShortVideo keeps the lighter stroke from ShortText. */}
      <style>{`
        .short-cover-title > div,
        .short-cover-title div[style*="text-transform: uppercase"] {
          -webkit-text-stroke: 1.6px rgba(0,0,0,0.7) !important;
          text-shadow:
            0 8px 22px rgba(0,0,0,0.65),
            0 2px 6px rgba(0,0,0,0.6),
            0 0 2px rgba(0,0,0,0.75) !important;
        }
      `}</style>
      <div className="short-cover-title" style={{position: 'absolute', inset: 0}}>
        <ShortHookLayout
          on_screen_text={String(title).toUpperCase()}
          caption={undefined}
          layout_payload={{
            title: String(title).toUpperCase(),
            subtitle: (layoutPayload as any).subtitle,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
