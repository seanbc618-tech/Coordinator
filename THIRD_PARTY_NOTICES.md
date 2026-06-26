# Third-Party Notices

## Hermes Agent

The Coordinator TUI adapts selected components from the Hermes Agent project
by Nous Research, licensed under the MIT License.

**Upstream:** https://github.com/NousResearch/hermes-agent
**License:** MIT
**Adapted components:**
- Terminal mode reset and cleanup (`ui-tui/src/lib/terminalModes.ts`)
- Graceful exit and signal handling (`ui-tui/src/lib/gracefulExit.ts`)
- Truecolor detection (`ui-tui/src/lib/forceTruecolor.ts`)
- Theme color system and light/dark detection (`ui-tui/src/theme.ts`)
- Entry point TTY guard and screen clearing (`ui-tui/src/entry.tsx`)
- Build script structure (`ui-tui/scripts/build.mjs`)

### MIT License

```
MIT License

Copyright (c) Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
