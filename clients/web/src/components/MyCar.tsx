import { useState } from "react";
import { MY_CAR } from "../garage";

const SWATCH = "#E8A317"; // Saffron in data/voltrieve.yaml

/** The customer's car. With little room it is a slim bar that opens, so that the chat stays first. */
export function MyCar({ owner }: { owner: string }) {
  const [open, setOpen] = useState(false);
  const car = MY_CAR;
  return (
    <section className="car" aria-label="Your car" data-open={open}>
      <p className="car-owner">Welcome, {owner}</p>
      <img
        className="car-photo"
        src={`${import.meta.env.BASE_URL}spark.webp`}
        width="1200"
        height="800"
        alt={`Your ${car.model} in ${car.color}`}
      />
      <div className="car-name">
        <h2>{car.model}</h2>
        <p>
          {car.registration} · {km(car.odometer_km)}
        </p>
      </div>
      <button
        type="button"
        className="ghost icon car-toggle"
        aria-expanded={open}
        aria-controls="car-facts"
        aria-label="Details of your car"
        onClick={() => setOpen(!open)}
      >
        <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
          <path d="m3 6 5 5 5-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      <dl className="car-facts" id="car-facts">
        <div>
          <dt>Registration</dt>
          <dd>
            <span className="plate">{car.registration}</span>
          </dd>
        </div>
        <div>
          <dt>Model year</dt>
          <dd>{car.model_year}</dd>
        </div>
        <div>
          <dt>Trim</dt>
          <dd>
            {car.trim}, {car.drive}
          </dd>
        </div>
        <div>
          <dt>Color</dt>
          <dd>
            <i className="swatch" style={{ background: SWATCH }} />
            {car.color}
          </dd>
        </div>
        <div>
          <dt>Odometer</dt>
          <dd>{km(car.odometer_km)}</dd>
        </div>
        <div>
          <dt>Next service</dt>
          <dd>{car.nextService}</dd>
        </div>
      </dl>
    </section>
  );
}

function km(value: number): string {
  return `${value.toLocaleString("en")} km`;
}
