import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  Logical,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";

export type DrawingTool = "none" | "trendline" | "zone" | "text";

export type DrawingPoint = { logical: Logical; price: number };

export type DrawingShape =
  | { id: number; kind: "line"; p1: DrawingPoint; p2: DrawingPoint }
  | { id: number; kind: "rect"; p1: DrawingPoint; p2: DrawingPoint }
  | { id: number; kind: "text"; p1: DrawingPoint; text: string };

function formatPrice(value: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

class DrawingPaneRenderer implements IPrimitivePaneRenderer {
  constructor(
    private shapes: DrawingShape[],
    private logicalToX: (logical: Logical) => number | null,
    private priceToY: (price: number) => number | null,
    private strokeColor: string,
    private fillColor: string,
    private textColor: string,
    private textBackground: string
  ) {}

  draw(target: CanvasRenderingTarget2D) {
    target.useMediaCoordinateSpace(({ context }) => {
      for (const shape of this.shapes) {
        if (shape.kind === "text") {
          const x = this.logicalToX(shape.p1.logical);
          const y = this.priceToY(shape.p1.price);
          if (x === null || y === null) {
            continue;
          }
          context.font = "12px sans-serif";
          const metrics = context.measureText(shape.text);
          const paddingX = 4;
          const paddingY = 3;
          const boxWidth = metrics.width + paddingX * 2;
          const boxHeight = 16 + paddingY;
          context.fillStyle = this.textBackground;
          context.fillRect(x, y - boxHeight / 2, boxWidth, boxHeight);
          context.strokeStyle = this.strokeColor;
          context.lineWidth = 1;
          context.strokeRect(x, y - boxHeight / 2, boxWidth, boxHeight);
          context.fillStyle = this.textColor;
          context.textBaseline = "middle";
          context.fillText(shape.text, x + paddingX, y + 1);
          continue;
        }

        const x1 = this.logicalToX(shape.p1.logical);
        const y1 = this.priceToY(shape.p1.price);
        const x2 = this.logicalToX(shape.p2.logical);
        const y2 = this.priceToY(shape.p2.price);
        if (x1 === null || y1 === null || x2 === null || y2 === null) {
          continue;
        }

        context.lineWidth = 2;
        context.strokeStyle = this.strokeColor;

        if (shape.kind === "line") {
          context.beginPath();
          context.moveTo(x1, y1);
          context.lineTo(x2, y2);
          context.stroke();
        } else {
          const x = Math.min(x1, x2);
          const y = Math.min(y1, y2);
          const w = Math.abs(x2 - x1);
          const h = Math.abs(y2 - y1);
          context.fillStyle = this.fillColor;
          context.fillRect(x, y, w, h);
          context.strokeRect(x, y, w, h);

          const lowPrice = Math.min(shape.p1.price, shape.p2.price);
          const highPrice = Math.max(shape.p1.price, shape.p2.price);
          const label = `${formatPrice(lowPrice)} – ${formatPrice(highPrice)}`;
          context.font = "12px sans-serif";
          const metrics = context.measureText(label);
          const paddingX = 4;
          const labelX = x + 4;
          const labelY = y + 4;
          context.fillStyle = this.textBackground;
          context.fillRect(labelX - paddingX, labelY, metrics.width + paddingX * 2, 16);
          context.fillStyle = this.textColor;
          context.textBaseline = "middle";
          context.fillText(label, labelX, labelY + 8);
        }
      }
    });
  }
}

class DrawingPaneView implements IPrimitivePaneView {
  constructor(private source: DrawingPrimitive) {}

  renderer(): IPrimitivePaneRenderer | null {
    const chart = this.source.getChart();
    const series = this.source.getSeries();
    if (!chart || !series) {
      return null;
    }
    return new DrawingPaneRenderer(
      this.source.allShapes(),
      (logical) => chart.timeScale().logicalToCoordinate(logical),
      (price) => series.priceToCoordinate(price),
      this.source.getStrokeColor(),
      this.source.getFillColor(),
      this.source.getTextColor(),
      this.source.getTextBackground()
    );
  }
}

export class DrawingPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType, Time> | null = null;
  private requestUpdateFn: (() => void) | null = null;
  private shapes: DrawingShape[] = [];
  private draft: DrawingShape | null = null;
  private strokeColor = "#4f5f45";
  private fillColor = "rgba(79, 95, 69, 0.15)";
  private textColor = "#edeef0";
  private textBackground = "rgba(27, 29, 33, 0.85)";
  private views: IPrimitivePaneView[] = [new DrawingPaneView(this)];

  attached(param: SeriesAttachedParameter<Time>) {
    this.chart = param.chart as IChartApi;
    this.series = param.series;
    this.requestUpdateFn = param.requestUpdate;
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.requestUpdateFn = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  getChart() {
    return this.chart;
  }

  getSeries() {
    return this.series;
  }

  getStrokeColor() {
    return this.strokeColor;
  }

  getFillColor() {
    return this.fillColor;
  }

  getTextColor() {
    return this.textColor;
  }

  getTextBackground() {
    return this.textBackground;
  }

  allShapes(): DrawingShape[] {
    return this.draft ? [...this.shapes, this.draft] : this.shapes;
  }

  setShapes(shapes: DrawingShape[]) {
    this.shapes = shapes;
    this.requestUpdateFn?.();
  }

  setDraft(draft: DrawingShape | null) {
    this.draft = draft;
    this.requestUpdateFn?.();
  }

  setColors(stroke: string, fill: string, text: string, textBackground: string) {
    this.strokeColor = stroke;
    this.fillColor = fill;
    this.textColor = text;
    this.textBackground = textBackground;
    this.requestUpdateFn?.();
  }
}
