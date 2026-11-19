import React from 'react';

export function Toggle(props) {
  return <span className="toggle">
    <input {...props} type="checkbox"/>
    <span className="toggle-track" aria-hidden="true"/>
  </span>;
}
