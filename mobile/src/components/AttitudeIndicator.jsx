import React from 'react';
import './AttitudeIndicator.css';

export default function AttitudeIndicator({ roll = null, pitch = null, heading = null }) {
  const hasData = roll !== null && pitch !== null;
  
  // Use 0 when no data, just for rendering the CSS safely (it will be covered by the NO DATA overlay)
  const safeRoll = hasData ? roll : 0;
  const safePitch = hasData ? pitch : 0;
  
  // 1 degree of pitch = 2.5 pixels translation
  const pitchScale = 2.5; 
  const pitchOffset = safePitch * pitchScale;

  return (
    <div className="attitude-indicator-wrapper">
      <div className="attitude-horizon-container">
        {!hasData && (
          <div className="no-data-overlay">NO ATTITUDE DATA</div>
        )}
        
        <div className="horizon-clip-mask">
          <div 
            className="horizon-plane"
            style={{ 
              transform: `rotate(${-safeRoll}deg) translateY(${pitchOffset}px)`
            }}
          >
            <div className="sky"></div>
            <div className="ground">
               <div className="pitch-ladder">
                  {[-40, -30, -20, -10, 0, 10, 20, 30, 40].map(deg => {
                    const isHorizon = deg === 0;
                    return (
                     <div 
                       key={deg} 
                       className={`pitch-line ${isHorizon ? 'horizon-line' : ''}`} 
                       style={{ top: `calc(50% - ${deg * pitchScale}px)` }}
                     >
                       {!isHorizon && <span className="pitch-num left">{Math.abs(deg)}</span>}
                       <div className={`pitch-bar ${isHorizon ? 'long' : 'short'}`}></div>
                       {!isHorizon && <span className="pitch-num right">{Math.abs(deg)}</span>}
                     </div>
                    );
                  })}
               </div>
            </div>
          </div>
        </div>
        
        {/* Fixed Center Reticle */}
        <div className="reticle">
           <div className="reticle-wing left"></div>
           <div className="reticle-center"></div>
           <div className="reticle-wing right"></div>
        </div>
        
        {/* Roll indicator markings at top */}
        <div className="roll-markings">
          {[-60, -45, -30, 0, 30, 45, 60].map(deg => (
            <div 
              key={deg} 
              className={`roll-tick ${deg === 0 ? 'center' : ''}`}
              style={{ transform: `rotate(${deg}deg)` }}
            ></div>
          ))}
          <div className="roll-pointer" style={{ transform: `rotate(${-safeRoll}deg)` }}></div>
        </div>
      </div>
      
      {/* Compass / Heading Tape */}
      <div className="heading-container">
         <div className="heading-readout">
            {heading !== null ? Math.round(heading).toString().padStart(3, '0') + '°' : '---°'}
         </div>
         <div className="compass-rose-wrapper">
           <div className="compass-rose" style={{ transform: `rotate(${- (heading || 0)}deg)` }}>
              <div className="compass-mark n">N</div>
              <div className="compass-mark e">E</div>
              <div className="compass-mark s">S</div>
              <div className="compass-mark w">W</div>
              {/* Intermediate ticks */}
              {[30, 60, 120, 150, 210, 240, 300, 330].map(deg => (
                <div key={deg} className="compass-tick" style={{ transform: `rotate(${deg}deg)` }}></div>
              ))}
           </div>
           <div className="compass-pointer"></div>
         </div>
      </div>
    </div>
  );
}
