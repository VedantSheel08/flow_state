// Texture atlas for sprites. Canvas 2D on the GPU batches consecutive
// drawImage calls only when they sample the same source image; drawing each
// vehicle from its own small canvas breaks every batch (measured on Chrome /
// ANGLE: 800 rotated sprites run at ~32 FPS from 13 canvases, ~59 FPS from one
// atlas). Sprites are therefore packed into a few large pages (shelf packing).

export interface AtlasRect {
  canvas: HTMLCanvasElement
  sx: number
  sy: number
  sw: number
  sh: number
}

const PAGE = 2048
const GAP = 2 // keeps bilinear filtering from bleeding between neighbours
const MAX_PAGES = 6

interface Page {
  canvas: HTMLCanvasElement
  ctx: CanvasRenderingContext2D
  x: number
  y: number
  rowH: number
}

export class Atlas {
  private pages: Page[] = []
  /** Bumped when the atlas is reset; holders of AtlasRects must drop them. */
  generation = 0

  private newPage(): Page {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = PAGE
    const page = { canvas, ctx: canvas.getContext('2d')!, x: 0, y: 0, rowH: 0 }
    this.pages.push(page)
    return page
  }

  /** Copy a rendered sprite into the atlas. Returns null if it is larger than a page. */
  add(src: HTMLCanvasElement): AtlasRect | null {
    const w = src.width
    const h = src.height
    if (w > PAGE || h > PAGE) return null
    let page = this.pages[this.pages.length - 1]
    if (page && page.x + w > PAGE) {
      page.x = 0
      page.y += page.rowH + GAP
      page.rowH = 0
    }
    if (!page || page.y + h > PAGE) {
      if (this.pages.length >= MAX_PAGES) this.reset()
      page = this.newPage()
    }
    const rect = { canvas: page.canvas, sx: page.x, sy: page.y, sw: w, sh: h }
    page.ctx.drawImage(src, page.x, page.y)
    page.x += w + GAP
    page.rowH = Math.max(page.rowH, h)
    return rect
  }

  reset() {
    this.pages = []
    this.generation++
  }

  get pageCount() {
    return this.pages.length
  }
}

export const atlas = new Atlas()
