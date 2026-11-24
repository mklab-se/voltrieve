import type { CSSProperties } from "react";
import type { Gauges } from "../gauges";
import { ParkingSign, WiperIcon } from "./icons";

const meters = (m: number) => `${m} m`;

export function DriverDisplay({ gauges, style }: { gauges: Gauges; style: CSSProperties }) {
  const { speed, gear, rangeKm, batteryPercent, wipers, signInM } = gauges;
  return (
    <div className="driver-display" style={style} role="group" aria-label="Driver display">
      <div className="dd-left">
        <p className="dd-gear" aria-label={`Gear ${gear}`}>
          {["P", "R", "D"].map((g) => (
            <span key={g} data-on={g === gear} aria-hidden="true">
              {g}
            </span>
          ))}
        </p>
        <p className="dd-glance" data-on={wipers !== "off"} data-fast={wipers === "fast"} aria-label={`Wipers ${wipers}`}>
          <WiperIcon />
          <span aria-hidden="true">{wipers === "off" ? "" : wipers}</span>
        </p>
      </div>
      <p className="dd-speed">
        <strong>{speed}</strong>
        <span>km/h</span>
      </p>
      <div className="dd-right">
        <p className="dd-range" aria-label={`Range ${rangeKm} kilometers, battery ${batteryPercent} percent`}>
          <strong>{rangeKm}</strong>
          <span>km</span>
          <i className="dd-battery" aria-hidden="true">
            <b style={{ width: `${batteryPercent}%` }} />
          </i>
        </p>
        <p className="dd-sign" aria-label={`Next sign in ${meters(signInM)}`}>
          <ParkingSign />
          <span>{meters(signInM)}</span>
        </p>
      </div>
    </div>
  );
}
