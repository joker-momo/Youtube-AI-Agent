import React from 'react';
import {AbsoluteFill} from 'remotion';
import {RenderProps} from './render-props';

export const Thumbnail: React.FC<RenderProps> = (props) => {
  const palette = props.style.palette;
  return (
    <AbsoluteFill style={{backgroundColor: palette.primary, fontFamily: 'Inter, Arial, sans-serif'}}>
      <div style={{position: 'absolute', inset: 0, background: `linear-gradient(135deg, ${palette.primary}, ${palette.secondary})`}} />
      <div style={{position: 'absolute', left: 70, right: 70, top: 72, color: palette.background}}>
        <div style={{fontSize: 42, fontWeight: 700, color: palette.accent}}>Vida Plena 45+</div>
        <div style={{fontSize: 92, lineHeight: 1.02, fontWeight: 900, marginTop: 58}}>DORMIR MEJOR</div>
        <div style={{fontSize: 70, lineHeight: 1.05, fontWeight: 800, marginTop: 18}}>DESPUES DE LOS 45</div>
      </div>
      <div style={{position: 'absolute', left: 70, bottom: 56, padding: '18px 28px', backgroundColor: palette.background, color: palette.text, fontSize: 34, fontWeight: 700}}>
        5 habitos simples
      </div>
    </AbsoluteFill>
  );
};
