// A QR code for a ZIP-321 payment URI, drawn as an SVG from qrcode-generator's module matrix. If the URI cannot be
// encoded (too long for a QR code, or the library failed), it degrades to showing the URI only: the payer can still
// copy it or open it in a wallet.

import qrcode from "qrcode-generator";
import { useMemo } from "react";

type Matrix = { size: number; path: string };

function encode(value: string): Matrix | null {
  try {
    const qr = qrcode(0, "M"); // 0 = smallest version that fits
    qr.addData(value, "Byte");
    qr.make();
    const size = qr.getModuleCount();
    let path = "";
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        if (qr.isDark(r, c)) path += `M${c} ${r}h1v1h-1z`;
      }
    }
    return { size, path };
  } catch {
    return null; // e.g. a multi-recipient request too long for one QR code
  }
}

export function QrCode({ value, size = 180, label = "Payment request QR code" }: { value: string; size?: number; label?: string }) {
  const matrix = useMemo(() => encode(value), [value]);
  if (!matrix) {
    return (
      <div className="qr qr-fallback">
        <p className="muted small">Too long for a QR code: copy the payment URI or open it in your wallet.</p>
        <code className="checkout-value">{value}</code>
      </div>
    );
  }
  const margin = 2;
  const box = matrix.size + margin * 2;
  return (
    <figure className="qr">
      <svg role="img" aria-label={label} width={size} height={size} viewBox={`${-margin} ${-margin} ${box} ${box}`} shapeRendering="crispEdges">
        <rect x={-margin} y={-margin} width={box} height={box} fill="#fff" />
        <path d={matrix.path} fill="#000" />
      </svg>
    </figure>
  );
}
