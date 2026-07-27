# Lessons

Mined automatically from the arena's trade and observation logs after
each round. Every line carries its sample size and p-value: a lesson
with thin support is a hypothesis, not a finding, and the transfer
logic weights it accordingly.

## Capability findings (these authorise a design change)

- **r9** `use_maker_first` — Resting orders earned +8.19c/contract for challenger while crossing earned -1.36c/contract for incumbent (12766/4730 contracts, p=0.000). Quoting inside the spread beats paying it, net of adverse selection. _(n=4730, effect=+9.558, p=0.000, confidence=1.00)_
- **r9** `use_market_prior` — challenger forecasts better than incumbent: Brier 0.1840 vs 0.1870 over 2000/2000 resolutions. Skill vs the market price: -0.008 vs -0.024. Blending the price into the estimate, rather than only subtracting it, is what closes that gap. _(n=2000, effect=+0.016, p=0.050, confidence=0.16)_
- **r9** `use_calibration` — Learning from every resolution, not only from settled trades, gave challenger 2000 training examples this run. Its forecasts are better calibrated as a result (Brier 0.1840 vs 0.1870). _(n=2000, effect=+0.003, p=0.010, confidence=0.06)_
- **r8** `use_maker_first` — Resting orders earned +8.33c/contract for challenger while crossing earned -3.21c/contract for incumbent (11124/4064 contracts, p=0.000). Quoting inside the spread beats paying it, net of adverse selection. _(n=4064, effect=+11.542, p=0.000, confidence=1.00)_
- **r8** `use_market_prior` — challenger forecasts better than incumbent: Brier 0.1829 vs 0.1852 over 1800/1800 resolutions. Skill vs the market price: -0.011 vs -0.023. Blending the price into the estimate, rather than only subtracting it, is what closes that gap. _(n=1800, effect=+0.012, p=0.050, confidence=0.12)_
- **r8** `use_calibration` — Learning from every resolution, not only from settled trades, gave challenger 1800 training examples this run. Its forecasts are better calibrated as a result (Brier 0.1829 vs 0.1852). _(n=1800, effect=+0.002, p=0.010, confidence=0.04)_

## Head-to-head contrasts

- **r9** `0-15c|resting|inefficient` — challenger beat incumbent by +26.89c per contract when resting in 0-15c markets (inefficient): +16.48c vs -10.41c over 1693/271 contracts (p=0.000). _(n=271, effect=+26.894, p=0.000, confidence=1.00)_
- **r9** `35-65c|crossing|inefficient` — challenger beat incumbent by +28.64c per contract when crossing in 35-65c markets (inefficient): +14.68c vs -13.96c over 524/329 contracts (p=0.000). _(n=329, effect=+28.640, p=0.000, confidence=1.00)_
- **r9** `15-35c|resting|efficient` — incumbent beat challenger by +9.16c per contract when resting in 15-35c markets (efficient): +9.66c vs +0.50c over 1312/2307 contracts (p=0.000). _(n=1312, effect=+9.163, p=0.000, confidence=1.00)_
- **r9** `35-65c|crossing|efficient` — incumbent beat challenger by +9.76c per contract when crossing in 35-65c markets (efficient): -5.16c vs -14.92c over 939/1016 contracts (p=0.000). _(n=939, effect=+9.756, p=0.000, confidence=1.00)_
- **r9** `35-65c|resting|efficient` — challenger beat incumbent by +5.77c per contract when resting in 35-65c markets (efficient): -4.47c vs -10.24c over 1871/1997 contracts (p=0.000). _(n=1871, effect=+5.773, p=0.000, confidence=1.00)_
- **r9** `15-35c|resting|inefficient` — challenger beat incumbent by +6.37c per contract when resting in 15-35c markets (inefficient): +12.45c vs +6.07c over 3130/651 contracts (p=0.002). _(n=651, effect=+6.372, p=0.002, confidence=0.99)_
- **r9** `35-65c|resting|inefficient` — challenger beat incumbent by +5.17c per contract when resting in 35-65c markets (inefficient): +17.15c vs +11.98c over 2233/889 contracts (p=0.006). _(n=889, effect=+5.172, p=0.006, confidence=0.97)_
- **r9** `65-85c|resting|inefficient` — challenger beat incumbent by +39.61c per contract when resting in 65-85c markets (inefficient): +23.39c vs -16.22c over 195/367 contracts (p=0.000). _(n=195, effect=+39.610, p=0.000, confidence=0.90)_
- **r9** `65-85c|crossing|efficient` — incumbent beat challenger by +18.15c per contract when crossing in 65-85c markets (efficient): -4.76c vs -22.91c over 547/189 contracts (p=0.000). _(n=189, effect=+18.148, p=0.000, confidence=0.89)_
- **r9** `65-85c|resting|efficient` — incumbent beat challenger by +11.41c per contract when resting in 65-85c markets (efficient): -3.92c vs -15.32c over 914/121 contracts (p=0.011). _(n=121, effect=+11.407, p=0.011, confidence=0.67)_
- **r9** `65-85c|crossing|inefficient` — challenger beat incumbent by +34.90c per contract when crossing in 65-85c markets (inefficient): +26.73c vs -8.17c over 92/129 contracts (p=0.000). _(n=92, effect=+34.898, p=0.000, confidence=0.62)_
- **r9** `0-15c|crossing|inefficient` — challenger beat incumbent by +5.09c per contract when crossing in 0-15c markets (inefficient): +14.61c vs +9.52c over 286/214 contracts (p=0.149). _(n=214, effect=+5.086, p=0.149, confidence=0.24)_
- **r8** `35-65c|crossing|inefficient` — challenger beat incumbent by +33.78c per contract when crossing in 35-65c markets (inefficient): +18.93c vs -14.86c over 416/281 contracts (p=0.000). _(n=281, effect=+33.784, p=0.000, confidence=1.00)_
- **r8** `15-35c|resting|efficient` — incumbent beat challenger by +7.20c per contract when resting in 15-35c markets (efficient): +8.37c vs +1.16c over 1144/2126 contracts (p=0.000). _(n=1144, effect=+7.203, p=0.000, confidence=1.00)_
- **r8** `35-65c|resting|inefficient` — challenger beat incumbent by +8.10c per contract when resting in 35-65c markets (inefficient): +18.77c vs +10.67c over 1967/721 contracts (p=0.000). _(n=721, effect=+8.097, p=0.000, confidence=1.00)_
- **r8** `35-65c|resting|efficient` — challenger beat incumbent by +6.39c per contract when resting in 35-65c markets (efficient): -3.03c vs -9.42c over 1646/1853 contracts (p=0.000). _(n=1646, effect=+6.386, p=0.000, confidence=1.00)_
- **r8** `35-65c|crossing|efficient` — incumbent beat challenger by +8.97c per contract when crossing in 35-65c markets (efficient): -6.26c vs -15.23c over 758/863 contracts (p=0.000). _(n=758, effect=+8.969, p=0.000, confidence=1.00)_
- **r8** `0-15c|resting|inefficient` — challenger beat incumbent by +25.63c per contract when resting in 0-15c markets (inefficient): +14.58c vs -11.05c over 1404/223 contracts (p=0.000). _(n=223, effect=+25.627, p=0.000, confidence=0.96)_
- **r8** `65-85c|resting|inefficient` — challenger beat incumbent by +35.95c per contract when resting in 65-85c markets (inefficient): +23.39c vs -12.56c over 195/271 contracts (p=0.000). _(n=195, effect=+35.951, p=0.000, confidence=0.90)_
- **r8** `15-35c|crossing|inefficient` — challenger beat incumbent by +11.91c per contract when crossing in 15-35c markets (inefficient): +21.62c vs +9.71c over 552/195 contracts (p=0.003). _(n=195, effect=+11.912, p=0.003, confidence=0.89)_
- **r8** `15-35c|resting|inefficient` — challenger beat incumbent by +4.28c per contract when resting in 15-35c markets (inefficient): +11.56c vs +7.27c over 2674/627 contracts (p=0.045). _(n=627, effect=+4.285, p=0.045, confidence=0.78)_
- **r8** `65-85c|crossing|efficient` — incumbent beat challenger by +37.27c per contract when crossing in 65-85c markets (efficient): -4.83c vs -42.10c over 476/139 contracts (p=0.000). _(n=139, effect=+37.270, p=0.000, confidence=0.76)_
- **r8** `0-15c|crossing|inefficient` — challenger beat incumbent by +8.76c per contract when crossing in 0-15c markets (inefficient): +9.99c vs +1.23c over 184/142 contracts (p=0.016). _(n=142, effect=+8.762, p=0.016, confidence=0.71)_
- **r8** `65-85c|crossing|inefficient` — challenger beat incumbent by +34.90c per contract when crossing in 65-85c markets (inefficient): +26.73c vs -8.17c over 92/129 contracts (p=0.000). _(n=92, effect=+34.898, p=0.000, confidence=0.62)_
- **r8** `65-85c|resting|efficient` — incumbent beat challenger by +9.42c per contract when resting in 65-85c markets (efficient): -5.90c vs -15.32c over 770/121 contracts (p=0.039). _(n=121, effect=+9.421, p=0.039, confidence=0.57)_

## Loss-making segments

- **r9** `0-15c|crossing|efficient` — incumbent: crossing in 0-15c markets (efficient regime) lost -3.87c per contract over 757 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=757, effect=-3.871, p=0.000, confidence=1.00)_
- **r9** `0-15c|resting|inefficient` — incumbent: resting in 0-15c markets (inefficient regime) lost -10.41c per contract over 271 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=271, effect=-10.413, p=0.000, confidence=1.00)_
- **r9** `35-65c|crossing|efficient` — incumbent: crossing in 35-65c markets (efficient regime) lost -5.16c per contract over 939 contracts (p=0.002). Widen the hurdle here or stop trading it. _(n=939, effect=-5.161, p=0.002, confidence=1.00)_
- **r9** `35-65c|crossing|inefficient` — incumbent: crossing in 35-65c markets (inefficient regime) lost -13.96c per contract over 329 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=329, effect=-13.960, p=0.000, confidence=1.00)_
- **r9** `35-65c|resting|efficient` — incumbent: resting in 35-65c markets (efficient regime) lost -10.24c per contract over 1997 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=1997, effect=-10.245, p=0.000, confidence=1.00)_
- **r9** `65-85c|crossing|efficient` — incumbent: crossing in 65-85c markets (efficient regime) lost -4.76c per contract over 547 contracts (p=0.013). Widen the hurdle here or stop trading it. _(n=547, effect=-4.764, p=0.013, confidence=1.00)_
- **r9** `65-85c|resting|efficient` — incumbent: resting in 65-85c markets (efficient regime) lost -3.92c per contract over 914 contracts (p=0.008). Widen the hurdle here or stop trading it. _(n=914, effect=-3.917, p=0.008, confidence=1.00)_
- **r9** `65-85c|resting|inefficient` — incumbent: resting in 65-85c markets (inefficient regime) lost -16.22c per contract over 367 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=367, effect=-16.223, p=0.000, confidence=1.00)_
- **r9** `0-15c|crossing|efficient` — challenger: crossing in 0-15c markets (efficient regime) lost -3.44c per contract over 726 contracts (p=0.001). Widen the hurdle here or stop trading it. _(n=726, effect=-3.436, p=0.001, confidence=1.00)_
- **r9** `35-65c|crossing|efficient` — challenger: crossing in 35-65c markets (efficient regime) lost -14.92c per contract over 1016 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=1016, effect=-14.917, p=0.000, confidence=1.00)_
- **r9** `35-65c|resting|efficient` — challenger: resting in 35-65c markets (efficient regime) lost -4.47c per contract over 1871 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=1871, effect=-4.472, p=0.000, confidence=1.00)_
- **r9** `65-85c|crossing|efficient` — challenger: crossing in 65-85c markets (efficient regime) lost -22.91c per contract over 189 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=189, effect=-22.912, p=0.000, confidence=0.79)_
- **r9** `65-85c|crossing|inefficient` — incumbent: crossing in 65-85c markets (inefficient regime) lost -8.17c per contract over 129 contracts (p=0.045). Widen the hurdle here or stop trading it. _(n=129, effect=-8.171, p=0.045, confidence=0.54)_
- **r9** `65-85c|resting|efficient` — challenger: resting in 65-85c markets (efficient regime) lost -15.32c per contract over 121 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=121, effect=-15.324, p=0.000, confidence=0.50)_
- **r8** `0-15c|crossing|efficient` — incumbent: crossing in 0-15c markets (efficient regime) lost -3.34c per contract over 690 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=690, effect=-3.336, p=0.000, confidence=1.00)_
- **r8** `35-65c|crossing|efficient` — incumbent: crossing in 35-65c markets (efficient regime) lost -6.26c per contract over 758 contracts (p=0.001). Widen the hurdle here or stop trading it. _(n=758, effect=-6.259, p=0.001, confidence=1.00)_
- **r8** `35-65c|crossing|inefficient` — incumbent: crossing in 35-65c markets (inefficient regime) lost -14.86c per contract over 281 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=281, effect=-14.858, p=0.000, confidence=1.00)_
- **r8** `35-65c|resting|efficient` — incumbent: resting in 35-65c markets (efficient regime) lost -9.42c per contract over 1853 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=1853, effect=-9.419, p=0.000, confidence=1.00)_
- **r8** `65-85c|crossing|efficient` — incumbent: crossing in 65-85c markets (efficient regime) lost -4.83c per contract over 476 contracts (p=0.019). Widen the hurdle here or stop trading it. _(n=476, effect=-4.834, p=0.019, confidence=1.00)_
- **r8** `65-85c|resting|efficient` — incumbent: resting in 65-85c markets (efficient regime) lost -5.90c per contract over 770 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=770, effect=-5.903, p=0.000, confidence=1.00)_
- **r8** `65-85c|resting|inefficient` — incumbent: resting in 65-85c markets (inefficient regime) lost -12.56c per contract over 271 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=271, effect=-12.565, p=0.000, confidence=1.00)_
- **r8** `85-100c|crossing|efficient` — incumbent: crossing in 85-100c markets (efficient regime) lost -4.55c per contract over 349 contracts (p=0.007). Widen the hurdle here or stop trading it. _(n=349, effect=-4.550, p=0.007, confidence=1.00)_
- **r8** `0-15c|crossing|efficient` — challenger: crossing in 0-15c markets (efficient regime) lost -4.13c per contract over 658 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=658, effect=-4.133, p=0.000, confidence=1.00)_
- **r8** `35-65c|crossing|efficient` — challenger: crossing in 35-65c markets (efficient regime) lost -15.23c per contract over 863 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=863, effect=-15.228, p=0.000, confidence=1.00)_
- **r8** `35-65c|resting|efficient` — challenger: resting in 35-65c markets (efficient regime) lost -3.03c per contract over 1646 contracts (p=0.012). Widen the hurdle here or stop trading it. _(n=1646, effect=-3.033, p=0.012, confidence=1.00)_
- **r8** `0-15c|resting|inefficient` — incumbent: resting in 0-15c markets (inefficient regime) lost -11.05c per contract over 223 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=223, effect=-11.049, p=0.000, confidence=0.93)_
- **r8** `65-85c|crossing|efficient` — challenger: crossing in 65-85c markets (efficient regime) lost -42.10c per contract over 139 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=139, effect=-42.104, p=0.000, confidence=0.58)_
- **r8** `65-85c|crossing|inefficient` — incumbent: crossing in 65-85c markets (inefficient regime) lost -8.17c per contract over 129 contracts (p=0.045). Widen the hurdle here or stop trading it. _(n=129, effect=-8.171, p=0.045, confidence=0.54)_
- **r8** `65-85c|resting|efficient` — challenger: resting in 65-85c markets (efficient regime) lost -15.32c per contract over 121 contracts (p=0.000). Widen the hurdle here or stop trading it. _(n=121, effect=-15.324, p=0.000, confidence=0.50)_

## Transfers applied

- **r8** `kelly_fraction` challenger → incumbent: 0.29568 → 0.29715 (weight 1.00)
- **r8** `max_pct_per_market` challenger → incumbent: 0.05072 → 0.05048 (weight 1.00)
- **r8** `max_single_order` challenger → incumbent: 24.27973 → 24.52462 (weight 1.00)
- **r7** `kelly_fraction` challenger → incumbent: 0.29345 → 0.29568 (weight 1.00)
- **r7** `max_pct_per_market` challenger → incumbent: 0.05109 → 0.05072 (weight 1.00)
- **r7** `max_single_order` challenger → incumbent: 23.90868 → 24.27973 (weight 1.00)
- **r6** `kelly_fraction` challenger → incumbent: 0.29008 → 0.29345 (weight 1.00)
- **r6** `max_pct_per_market` challenger → incumbent: 0.05165 → 0.05109 (weight 1.00)
- **r6** `max_single_order` challenger → incumbent: 23.34648 → 23.90868 (weight 1.00)
- **r5** `kelly_fraction` challenger → incumbent: 0.28497 → 0.29008 (weight 1.00)
- **r5** `max_pct_per_market` challenger → incumbent: 0.05251 → 0.05165 (weight 1.00)
- **r5** `max_single_order` challenger → incumbent: 22.49467 → 23.34648 (weight 1.00)
- **r4** `kelly_fraction` challenger → incumbent: 0.27722 → 0.28497 (weight 1.00)
- **r4** `max_pct_per_market` challenger → incumbent: 0.0538 → 0.05251 (weight 1.00)
- **r4** `max_single_order` challenger → incumbent: 21.20405 → 22.49467 (weight 1.00)
- **r3** `kelly_fraction` challenger → incumbent: 0.26549 → 0.27722 (weight 1.00)
- **r3** `max_pct_per_market` challenger → incumbent: 0.05575 → 0.0538 (weight 1.00)
- **r3** `max_single_order` challenger → incumbent: 19.24855 → 21.20405 (weight 1.00)
- **r2** `kelly_fraction` challenger → incumbent: 0.24771 → 0.26549 (weight 1.00)
- **r2** `max_pct_per_market` challenger → incumbent: 0.05871 → 0.05575 (weight 1.00)
- **r2** `max_single_order` challenger → incumbent: 16.28569 → 19.24855 (weight 1.00)
- **r1** `kelly_fraction` challenger → incumbent: 0.22078 → 0.24771 (weight 1.00)
- **r1** `max_pct_per_market` challenger → incumbent: 0.0632 → 0.05871 (weight 1.00)
- **r1** `max_single_order` challenger → incumbent: 11.7965 → 16.28569 (weight 1.00)
- **r0** `use_maker_first` challenger → incumbent: False → True (weight 0.98)
- **r0** `kelly_fraction` challenger → incumbent: 0.18 → 0.22078 (weight 1.00)
- **r0** `max_pct_per_market` challenger → incumbent: 0.07 → 0.0632 (weight 1.00)
- **r0** `max_single_order` challenger → incumbent: 5.0 → 11.7965 (weight 1.00)
