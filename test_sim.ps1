$env:DRONEOS_PROFILE="sim"

$job1 = Start-Job -ScriptBlock { $env:DRONEOS_PROFILE="sim"; cd "d:\CityGrid\my-project\PhoneOS_Swarm"; python start_drone1.py --ws-port 8080 2>&1 > logs\job1.log }
$job2 = Start-Job -ScriptBlock { $env:DRONEOS_PROFILE="sim"; cd "d:\CityGrid\my-project\PhoneOS_Swarm"; python start_drone2.py --ws-port 8081 2>&1 > logs\job2.log }
$job3 = Start-Job -ScriptBlock { $env:DRONEOS_PROFILE="sim"; cd "d:\CityGrid\my-project\PhoneOS_Swarm"; python start_drone3.py --ws-port 8082 2>&1 > logs\job3.log }
$job4 = Start-Job -ScriptBlock { $env:DRONEOS_PROFILE="sim"; cd "d:\CityGrid\my-project\PhoneOS_Swarm"; python start_drone4.py --ws-port 8083 2>&1 > logs\job4.log }

Start-Sleep -Seconds 10
netstat -ano | findstr :808

Stop-Job -Name $job1.Name
Stop-Job -Name $job2.Name
Stop-Job -Name $job3.Name
Stop-Job -Name $job4.Name
