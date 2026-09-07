@echo off
REM Ejecutado por el Programador de tareas de Windows cada 30 min en horario
REM laboral (ver README, seccion "Comprobacion automatica"). Encadena:
REM   1) buscar respuestas nuevas en Gmail
REM   2) analizarlas con IA
REM   3) recalcular el ranking
REM Cada paso es seguro de repetir (no reprocesa lo ya hecho), asi que no pasa
REM nada si no hay nada nuevo en una pasada.
cd /d C:\permiso_b\buscador
echo ==== %date% %time% ==== >> logs\scheduled_check.log
".venv\Scripts\python.exe" -m app.cli check-replies >> logs\scheduled_check.log 2>&1
".venv\Scripts\python.exe" -m app.cli process-replies >> logs\scheduled_check.log 2>&1
".venv\Scripts\python.exe" -m app.cli evaluate-all >> logs\scheduled_check.log 2>&1
echo ==== fin ==== >> logs\scheduled_check.log
