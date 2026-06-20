import React from 'react';
import {AbsoluteFill} from 'remotion';
import {SHORT_OVERLAYS, OverlayKey} from './ShortLayoutConstants';

export const ShortReadabilityOverlay: React.FC<{overlay?: OverlayKey}> = ({overlay = 'default'}) => {
  const ov = SHORT_OVERLAYS[overlay] ?? SHORT_OVERLAYS.default;
  return (
    <>
      <AbsoluteFill style={{backgroundColor: `rgba(0,0,0,${ov.fullDarkenOpacity})`}} />
      <AbsoluteFill
        style={{
          background: `linear-gradient(180deg, rgba(0,0,0,0) 55%, rgba(0,0,0,${ov.bottomGradientOpacity}) 100%)`,
        }}
      />
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at center, rgba(0,0,0,${ov.centerTextScrimOpacity}) 0%, rgba(0,0,0,0) 65%)`,
        }}
      />
    </>
  );
};
