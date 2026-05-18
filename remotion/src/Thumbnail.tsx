import React from 'react';
import {AbsoluteFill} from 'remotion';
import {RenderProps} from './render-props';
import {fitHeadline, softShadow} from './styles';

export const Thumbnail: React.FC<RenderProps> = (props) => {
  const palette = props.style.palette;
  const leadScene = props.scenes[0];
  const title = props.seo.title.toUpperCase();
  const supportText = (leadScene?.on_screen_text || props.channel.description).toUpperCase();
  const headlineSize = fitHeadline(title, 78, 48);
  const supportSize = fitHeadline(supportText, 54, 36);

  return (
    <AbsoluteFill style={{backgroundColor: palette.primary, fontFamily: 'Inter, Arial, sans-serif', overflow: 'hidden'}}>
      <div style={{position: 'absolute', inset: 0, background: `linear-gradient(135deg, ${palette.primary} 0%, ${palette.secondary} 74%)`}} />
      <div style={{position: 'absolute', right: -80, top: 0, width: 420, height: 820, backgroundColor: 'rgba(246,241,232,0.16)', transform: 'rotate(14deg)'}} />
      <div style={{position: 'absolute', right: 108, top: -80, width: 86, height: 900, backgroundColor: palette.accent, transform: 'rotate(14deg)', opacity: 0.72}} />
      <div style={{position: 'absolute', left: 70, right: 70, top: 58, color: palette.background}}>
        <div style={{display: 'inline-flex', alignItems: 'center', gap: 18, padding: '14px 20px', backgroundColor: 'rgba(38,51,47,0.44)', boxShadow: softShadow}}>
          <span style={{width: 18, height: 18, borderRadius: 9, backgroundColor: palette.accent}} />
          <span style={{fontSize: 32, fontWeight: 800}}>{props.channel.name}</span>
        </div>
        <div style={{fontSize: headlineSize, lineHeight: 1.01, fontWeight: 950, marginTop: 54, width: 900, textTransform: 'uppercase'}}>
          {title}
        </div>
        <div style={{fontSize: supportSize, lineHeight: 1.04, fontWeight: 850, marginTop: 22, width: 760, color: palette.accent}}>
          {supportText}
        </div>
      </div>
      <div style={{position: 'absolute', left: 70, bottom: 50, padding: '18px 28px', backgroundColor: palette.background, color: palette.text, fontSize: 31, fontWeight: 850, boxShadow: softShadow}}>
        {props.scenes.length} escenas practicas
      </div>
    </AbsoluteFill>
  );
};
