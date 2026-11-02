# Hero video

The video on the home page of the aws-analyzer guide, made with [Remotion](https://www.remotion.dev/). The scenes are
in `src/scenes/`; their text comes from the guide's demo reports.

```bash
npm install
npm run dev      # preview at http://localhost:3000
npm run render   # writes ../docs/images/hero-demo.mp4 and hero-poster.webp
```

`npx remotion skills add` installs Remotion's skills for coding agents; they stay out of git.
