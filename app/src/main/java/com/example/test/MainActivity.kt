package com.example.test

import android.os.Bundle
import android.util.Log
import androidx.activity.enableEdgeToEdge
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContentView(R.layout.activity_main)
        val data = 10
        Log.i("LoginActivity","login button clicked")
        Log.i("LoginRepository","send login details")
        Log.i("LoginService","receive login details, user hey there ${data}")
        Log.i("LoginService","send login response")
        Log.i("LoginRepository","receive login response")
        // New log
        Log.i("LoginRepository","receive login response")

        getUserDetails()!!.subSequence(0,1)

        ViewCompat.setOnApplyWindowInsetsListener(findViewById(R.id.main)) { v, insets ->
            val systemBars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }
    }

    fun getUserDetails(): String? = null
}