import React from 'react';
import {ChannelVideo} from './ChannelVideo';
import {RenderProps} from './render-props';

/**
 * Vertical (1080x1920) YouTube Short. Reuses the long-form ChannelVideo
 * renderer; verticality comes from props.render.resolution = "1080x1920"
 * (see calculateMetadata in Root.tsx). Vertical safe zones and caption
 * sizing follow the ChannelVideo subtitle config, which already honours the
 * composition height.
 */
export const ShortVideo: React.FC<RenderProps> = (props) => {
  return <ChannelVideo {...props} />;
};
