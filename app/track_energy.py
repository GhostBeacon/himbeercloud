import os
import sqlite3
import re
import subprocess
from datetime import datetime

# Pauschale Schaetzung fuer externe Festplatten (kein eigener Stromsensor vorhanden): 0 ohne
# Platte, der Einrichtungsassistent setzt 7 W je Platte. Wird zur echten Pi-Messung dazugerechnet.
HDD_WATTS = float(os.environ.get("RASPICLOUD_HDD_WATTS", "0"))

DB_PATH = os.environ.get("RASPICLOUD_DB", "/var/lib/raspicloud/users.db")


def get_pi_power_watts():
    """Liest die echten Strom-/Spannungswerte des Raspberry Pi 5 ueber den eingebauten
    PMIC-Sensor aus ('vcgencmd pmic_read_adc') und berechnet daraus die aktuelle
    Gesamtleistung in Watt (Summe aus Spannung mal Strom ueber alle internen
    Versorgungsschienen: CPU, RAM, WLAN, HDMI usw.).

    Gibt None zurueck, falls vcgencmd nicht verfuegbar ist oder das Auslesen fehlschlaegt."""
    try:
        result = subprocess.run(['vcgencmd', 'pmic_read_adc'], capture_output=True, text=True, timeout=3)
        output = result.stdout

        currents = {}
        voltages = {}
        pattern = re.compile(r'^\s*(\S+)_(A|V)\s+(?:current|volt)\(\d+\)=([\-0-9.]+)[AV]', re.MULTILINE)
        for match in pattern.finditer(output):
            rail, kind, value = match.group(1), match.group(2), float(match.group(3))
            if kind == 'A':
                currents[rail] = value
            else:
                voltages[rail] = value

        if not currents:
            return None  # kein PMIC-Sensor (z.B. Raspberry Pi 4)
        total_watts = sum(currents[rail] * voltages[rail] for rail in currents if rail in voltages)
        return round(total_watts, 2)
    except Exception:
        return None


def main():
    pi_watts = get_pi_power_watts()
    if pi_watts is None:
        pi_watts = 4.0  # Notfall-Rueckfallwert, falls das PMIC-Auslesen einmal fehlschlaegt

    power_watts = pi_watts + HDD_WATTS

    # Skript laeuft per Cronjob im 1-Minuten-Takt
    interval_hours = 1 / 60
    kwh_increment = (power_watts / 1000) * interval_hours

    today = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute(
        """INSERT INTO energy_usage (date, kwh) VALUES (?, ?)
           ON CONFLICT(date) DO UPDATE SET kwh = kwh + excluded.kwh""",
        (today, kwh_increment)
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
