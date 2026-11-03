/**
 * Note: When using the Node.JS APIs, the config file
 * doesn't apply. Instead, pass options directly to the APIs.
 *
 * All configuration options: https://remotion.dev/docs/config
 */

import { Config } from "@remotion/cli/config";

Config.setRspack(true);
Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setDefaultPremountInSeconds(2);

/*
 * Keep the hero video playable on every browser's hardware decoder: H.264 level 4.0 with at most 4 reference
 * frames (x264's "slower" preset picks 8, which needs level 5.0 at 1280x960 and makes some decoders stall), a
 * keyframe every 3 seconds so a replay or seek starts at once, and the index at the front so it plays while it loads.
 */
Config.overrideFfmpegCommand(({ type, args }) => {
  if (type !== "stitcher") return args;
  const output = args[args.length - 1];
  return [
    ...args.slice(0, -1),
    "-level:v", "4.0",
    "-x264-params", "ref=4:keyint=90:min-keyint=30",
    "-movflags", "+faststart",
    output,
  ];
});
