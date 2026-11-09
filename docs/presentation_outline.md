# Results presentation (10 minutes)

A slide-by-slide outline for presenting LoadLens to managers. Aim for one message per slide.

| # | Slide | Message | Time |
|---|---|---|---|
| 1 | **Title** | LoadLens: simulating and improving the parcel preload. Your name, date. | 0:20 |
| 2 | **The problem** | Preload runs on a hard deadline. Surges create backlogs, backlogs create mistakes, and a misload costs an extra trip. Use a simple belt-and-vans sketch. | 1:00 |
| 3 | **Why it matters** | Every missed or misloaded package is a failed delivery promise and extra cost. Small percentages add up across thousands of packages a day. | 0:40 |
| 4 | **What I built** | Simulator + sensor-based detection + capacity forecast + optimizer + database + dashboard. One architecture diagram. | 1:00 |
| 5 | **Finding 1: pressure drives mistakes** | Misload rate goes from 1.3% to 4.3% as the backlog grows. Show the bar chart. | 1:00 |
| 6 | **Finding 2: sensors catch wrong-van loads, not wrong-shelf loads** | 98% of wrong-van loads caught; wrong-shelf loads make up most escaped misloads. Suggests shelf-level checks as a next step. | 1:00 |
| 7 | **Finding 3: van overflow is predictable** | The manifest flags overflowing vans before loading starts, with a concrete move suggestion. Show the van fill chart. | 0:50 |
| 8 | **The fix: rebalance vans across zones** | Optimizer moves 11 of 20 vans. Missed packages 77 → 0, average wait 4.0 → 1.7 min, misload rate 2.46% → 2.00%. Before/after table. | 1:30 |
| 9 | **Live demo** | Switch preset to "Peak season", rerun, show the Optimizer tab. Keep it under 90 seconds. | 1:30 |
| 10 | **Limitations and next steps** | Synthetic data, one loader per zone, simple error model. Next: shelf-level sensing, floating helpers, live re-optimization, validation with real data. | 0:40 |
| 11 | **Close** | What I learned, and a link to the GitHub repository and live app. | 0:30 |

## Delivery tips

- Open with the problem in everyday terms, not with the technology.
- Put the before/after numbers in large type; keep code off the slides.
- Rehearse the demo and keep screenshots as a backup in case the network fails.
- Expect questions on how realistic the data is: point to the settings and the limitations slide.
- End with one concrete recommendation.
