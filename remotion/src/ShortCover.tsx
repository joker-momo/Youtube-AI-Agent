import React from 'react';
import {AbsoluteFill} from 'remotion';
import {RenderProps} from './render-props';

/**
 * Vertical Short cover (1080x1920). Renders the first scene's on-screen hook
 * over a dark backdrop. Typography: cover hook Montserrat 900. The renderer
 * primarily extracts a cover frame from short.mp4, but this composition exists
 * so a standalone cover can be rendered when desired.
 */
export const ShortCover: React.FC<RenderProps> = (props) => {
  const scenes = (props as any)?.scenes || (props as any)?.scene_doc?.scenes || [];
  const first = scenes[0] || {};
  const hook = (first.on_screen_text || (props as any)?.script?.hook || '').toString().toUpperCase();
  return (
    <AbsoluteFill style={{backgroundColor: '#0b1020', justifyContent: 'center', alignItems: 'center', padding: 80}}>
      <div
        style={{
          fontFamily: 'Montserrat, sans-serif',
          fontWeight: 900,
          fontSize: 120,
          lineHeight: 1.05,
          color: '#ffffff',
          textAlign: 'center',
          textShadow: '0 6px 30px rgba(0,0,0,0.6)',
        }}
      >
        {hook}
      </div>
    </AbsoluteFill>
  );
};
