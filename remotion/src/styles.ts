import {CSSProperties} from 'react';

export const fullFrame: CSSProperties = {
  flex: 1,
  width: '100%',
  height: '100%',
  position: 'relative',
  overflow: 'hidden',
  fontFamily: 'Manrope, "Plus Jakarta Sans", "Segoe UI", Arial, sans-serif',
};

export const softShadow = '0 22px 80px rgba(18, 29, 25, 0.28)';

export const fitHeadline = (text: string, base: number, min: number): number => {
  const normalizedLength = text.trim().length;
  if (normalizedLength <= 18) {
    return base;
  }
  return Math.max(min, base - Math.ceil((normalizedLength - 18) * 1.25));
};
