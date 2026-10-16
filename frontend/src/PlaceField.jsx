import { useEffect, useRef, useState } from "react";
import { searchPlaces } from "./api";

export default function PlaceField({ id, label, value, onChange }) {
  const [options, setOptions] = useState([]);
  const [open, setOpen] = useState(false);
  const edited = useRef(false);
  const requestId = useRef(0);

  useEffect(() => {
    if (!edited.current) return undefined;
    const query = value.trim();
    if (query.length < 2) {
      setOptions([]);
      setOpen(false);
      return undefined;
    }
    const current = ++requestId.current;
    const timer = setTimeout(async () => {
      const places = await searchPlaces(query);
      if (current !== requestId.current) return;
      setOptions(places);
      setOpen(places.length > 0);
    }, 300);
    return () => clearTimeout(timer);
  }, [value]);

  function choose(place) {
    edited.current = false;
    onChange(place.label);
    setOptions([]);
    setOpen(false);
  }

  return (
    <div className="place">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        value={value}
        placeholder="Start typing a city"
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        onChange={(event) => {
          edited.current = true;
          onChange(event.target.value);
        }}
        onFocus={() => options.length > 0 && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        required
      />
      {open && (
        <ul className="menu" role="listbox">
          {options.map((place) => (
            <li key={`${place.label}-${place.lat}`}>
              <button type="button" role="option" onMouseDown={() => choose(place)}>
                {place.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
