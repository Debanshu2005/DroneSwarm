$env:DRONEOS_PROFILE="sim"

$job1 = Start-Job -ScriptBlock { $env:DRONEOS_PROFILE="sim"; cd "d:\CityGrid\my-project\PhoneOS_Swarm"; python start_drone1.py *>&1 | Out-File logs\job1.log }
$job2 = Start-Job -ScriptBlock { $env:DRONEOS_PROFILE="sim"; cd "d:\CityGrid\my-project\PhoneOS_Swarm"; python start_drone2.py *>&1 | Out-File logs\job2.log }
$job3 = Start-Job -ScriptBlock { $env:DRONEOS_PROFILE="sim"; cd "d:\CityGrid\my-project\PhoneOS_Swarm"; python start_drone3.py *>&1 | Out-File logs\job3.log }
$job4 = Start-Job -ScriptBlock { $env:DRONEOS_PROFILE="sim"; cd "d:\CityGrid\my-project\PhoneOS_Swarm"; python start_drone4.py *>&1 | Out-File logs\job4.log }

Start-Sleep -Seconds 10
netstat -ano | findstr ":8080 :8081 :8082 :8083 :14550 :14551 :14552 :14553 :14650 :14651 :14652 :14653"

Stop-Job -Name $job1.Name
Stop-Job -Name $job2.Name
Stop-Job -Name $job3.Name
Stop-Job -Name $job4.Name
