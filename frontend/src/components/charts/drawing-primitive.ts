import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";

export type DrawingTool =
  | "none"
  | "trendline"
  | "ray"
  | "horizontal"
  | "vertical"
  | "zone"
  | "fib"
  | "measure"
  | "arrow"
  | "text"
  | "eraser";

export type DrawingPoint = { time: Time; price: number };

export type DrawingShape =
  | { id: number; kind: "line" | "ray" | "arrow" | "rect" | "fib" | "measure"; p1: DrawingPoint; p2: DrawingPoint }
  | { id: number; kind: "horizontal" | "vertical"; p1: DrawingPoint }
  | { id: number; kind: "text"; p1: DrawingPoint; text: string };

function formatPrice(value: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function drawLabel(context: CanvasRenderingContext2D, text: string, x: number, y: number, textColor: string, background: string) {
  context.font = "12px sans-serif";
  const metrics = context.measureText(text);
  const width = metrics.width + 10;
  const height = 20;
  context.fillStyle = background;
  context.fillRect(x, y - height / 2, width, height);
  context.fillStyle = textColor;
  context.textBaseline = "middle";
  context.fillText(text, x + 5, y);
}

class DrawingPaneRenderer implements IPrimitivePaneRenderer {
  constructor(
    private shapes: DrawingShape[],
    private timeToX: (time: Time) => number | null,
    private priceToY: (price: number) => number | null,
    private strokeColor: string,
    private fillColor: string,
    private textColor: string,
    private textBackground: string,
  ) {}

  draw(target: CanvasRenderingTarget2D) {
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      context.lineWidth = 1.5;
      context.strokeStyle = this.strokeColor;
      context.fillStyle = this.fillColor;

      for (const shape of this.shapes) {
        const x1 = this.timeToX(shape.p1.time);
        const y1 = this.priceToY(shape.p1.price);
        if (x1 === null || y1 === null) continue;

        if (shape.kind === "horizontal") {
          context.beginPath();
          context.moveTo(0, y1);
          context.lineTo(mediaSize.width, y1);
          context.stroke();
          drawLabel(context, formatPrice(shape.p1.price), Math.max(4, mediaSize.width - 86), y1 - 12, this.textColor, this.textBackground);
          continue;
        }

        if (shape.kind === "vertical") {
          context.beginPath();
          context.moveTo(x1, 0);
          context.lineTo(x1, mediaSize.height);
          context.stroke();
          continue;
        }

        if (shape.kind === "text") {
          drawLabel(context, shape.text, x1, y1, this.textColor, this.textBackground);
          continue;
        }

        if (!("p2" in shape)) continue;
        const x2 = this.timeToX(shape.p2.time);
        const y2 = this.priceToY(shape.p2.price);
        if (x2 === null || y2 === null) continue;

        if (shape.kind === "rect") {
          const x = Math.min(x1, x2);
          const y = Math.min(y1, y2);
          const width = Math.abs(x2 - x1);
          const height = Math.abs(y2 - y1);
          context.fillStyle = this.fillColor;
          context.fillRect(x, y, width, height);
          context.strokeStyle = this.strokeColor;
          context.strokeRect(x, y, width, height);
          drawLabel(
            context,
            `${formatPrice(Math.min(shape.p1.price, shape.p2.price))} – ${formatPrice(Math.max(shape.p1.price, shape.p2.price))}`,
            x + 4,
            y + 12,
            this.textColor,
            this.textBackground,
          );
          continue;
        }

        if (shape.kind === "fib") {
          const ratios = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
          const high = shape.p1.price;
          const low = shape.p2.price;
          for (const ratio of ratios) {
            const price = high - ratio * (high - low);
            const y = this.priceToY(price);
            if (y === null) continue;
            context.globalAlpha = ratio === 0 || ratio === 1 ? 1 : 0.75;
            context.beginPath();
            context.moveTo(Math.min(x1, x2), y);
            context.lineTo(Math.max(x1, x2), y);
            context.stroke();
            drawLabel(context, `${(ratio * 100).toFixed(1)}%  ${formatPrice(price)}`, Math.min(x1, x2) + 4, y - 10, this.textColor, this.textBackground);
          }
          context.globalAlpha = 1;
          continue;
        }

        if (shape.kind === "measure") {
          const x = Math.min(x1, x2);
          const y = Math.min(y1, y2);
          const width = Math.abs(x2 - x1);
          const height = Math.abs(y2 - y1);
          context.fillStyle = this.fillColor;
          context.fillRect(x, y, width, height);
          context.strokeRect(x, y, width, height);
          const change = shape.p1.price === 0 ? 0 : ((shape.p2.price - shape.p1.price) / shape.p1.price) * 100;
          drawLabel(
            context,
            `${shape.p2.price - shape.p1.price >= 0 ? "+" : ""}${formatPrice(shape.p2.price - shape.p1.price)}  (${change >= 0 ? "+" : ""}${change.toFixed(2)}%)`,
            x + 4,
            y + 12,
            this.textColor,
            this.textBackground,
          );
          continue;
        }

        context.beginPath();
        context.moveTo(x1, y1);
        if (shape.kind === "ray" && x2 !== x1) {
          const slope = (y2 - y1) / (x2 - x1);
          const targetX = x2 > x1 ? mediaSize.width : 0;
          const targetY = y1 + slope * (targetX - x1);
          context.lineTo(targetX, targetY);
        } else {
          context.lineTo(x2, y2);
        }
        context.stroke();

        if (shape.kind === "arrow") {
          const angle = Math.atan2(y2 - y1, x2 - x1);
          const length = 9;
          context.beginPath();
          context.moveTo(x2, y2);
          context.lineTo(x2 - length * Math.cos(angle - Math.PI / 6), y2 - length * Math.sin(angle - Math.PI / 6));
          context.moveTo(x2, y2);
          context.lineTo(x2 - length * Math.cos(angle + Math.PI / 6), y2 - length * Math.sin(angle + Math.PI / 6));
          context.stroke();
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
    if (!chart || !series) return null;
    return new DrawingPaneRenderer(
      this.source.allShapes(),
      (time) => chart.timeScale().timeToCoordinate(time),
      (price) => series.priceToCoordinate(price),
      this.source.getStrokeColor(),
      this.source.getFillColor(),
      this.source.getTextColor(),
      this.source.getTextBackground(),
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
  private fillColor = "rgba(79,95,69,0.15)";
  private textColor = "#edeef0";
  private textBackground = "rgba(27,29,33,0.9)";
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

  paneViews(): readonly IPrimitivePaneView[] { return this.views; }
  getChart() { return this.chart; }
  getSeries() { return this.series; }
  getStrokeColor() { return this.strokeColor; }
  getFillColor() { return this.fillColor; }
  getTextColor() { return this.textColor; }
  getTextBackground() { return this.textBackground; }
  allShapes(): DrawingShape[] { return this.draft ? [...this.shapes, this.draft] : this.shapes; }

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
